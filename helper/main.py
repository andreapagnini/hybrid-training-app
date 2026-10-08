"""Private helper for the Hybrid Training app: sends planned workouts to Garmin Connect.

Every /api call needs "Authorization: Bearer <APP_KEY>". See README.md.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from account import GarminAccount, NotConnected
from store import open_store
from sync import Syncer

HELPER_VERSION = "0.1.0"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("helper")

APP_KEY = os.environ.get("APP_KEY", "")
STORE_URL = os.environ.get("STORE_URL", str(Path(__file__).parent / "data"))
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
APP_DIR = os.environ.get("APP_DIR", "")
if len(APP_KEY) < 32:
    raise SystemExit("APP_KEY must be set to a random secret of at least 32 characters")

store = open_store(STORE_URL)
account = GarminAccount(store)
sync_lock = threading.Lock()

app = FastAPI(title="Hybrid Training helper", docs_url=None, redoc_url=None, openapi_url=None)
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=86400,
    )


def require_key(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token.encode(), APP_KEY.encode()):
        raise HTTPException(status_code=401, detail="wrong_app_key")


def _garmin_error(e: Exception) -> HTTPException:
    """Turn toolkit errors into short codes the app can show in plain words."""
    name = type(e).__name__
    if isinstance(e, NotConnected):
        return HTTPException(status_code=409, detail="not_connected")
    if "Authentication" in name:
        account.forget_client()
        return HTTPException(status_code=401, detail="garmin_sign_in_failed")
    if "TooManyRequests" in name:
        return HTTPException(status_code=429, detail="garmin_busy")
    log.warning("Garmin error: %s", name)
    return HTTPException(status_code=502, detail="garmin_error")


def _record_meta(**fields: Any) -> dict:
    meta = store.get_json("meta.json", {})
    meta.update(fields)
    store.put_json("meta.json", meta)
    return meta


# -- API -----------------------------------------------------------------------
class Login(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class Mfa(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class Tokens(BaseModel):
    tokens: str = Field(min_length=10, max_length=20000)


class SyncRequest(BaseModel):
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    workouts: list[dict[str, Any]] = Field(max_length=500)


class Disconnect(BaseModel):
    removeWorkouts: bool = False


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": HELPER_VERSION}


@app.get("/api/status", dependencies=[Depends(require_key)])
def status() -> dict:
    meta = store.get_json("meta.json", {})
    return {"version": HELPER_VERSION, "connected": account.connected(), **meta}


@app.post("/api/garmin/login", dependencies=[Depends(require_key)])
def login(body: Login) -> dict:
    try:
        state = account.start_login(body.email.strip(), body.password)
    except Exception as e:
        raise _garmin_error(e) from None
    if state == "connected":
        _record_meta(connectedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), garminName=account.display_name())
    return {"state": state}


@app.post("/api/garmin/mfa", dependencies=[Depends(require_key)])
def mfa(body: Mfa) -> dict:
    try:
        account.finish_mfa(body.code.strip())
    except Exception as e:
        raise _garmin_error(e) from None
    _record_meta(connectedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), garminName=account.display_name())
    return {"state": "connected"}


@app.post("/api/garmin/tokens", dependencies=[Depends(require_key)])
def import_tokens(body: Tokens) -> dict:
    try:
        account.import_tokens(body.tokens)
    except Exception as e:
        account.disconnect()
        raise _garmin_error(e) from None
    _record_meta(connectedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), garminName=account.display_name())
    return {"state": "connected"}


@app.post("/api/garmin/disconnect", dependencies=[Depends(require_key)])
def disconnect(body: Disconnect) -> dict:
    removed = 0
    with sync_lock:
        if body.removeWorkouts and account.connected():
            try:
                removed = Syncer(store, account.client()).remove_all()
            except Exception as e:
                raise _garmin_error(e) from None
        account.disconnect()
    store.put_json("meta.json", {})
    return {"state": "disconnected", "removed": removed}


@app.get("/api/exercises")
def exercises() -> JSONResponse:
    """Garmin's strength exercise list: name, category, exercise."""
    from garminconnect.exercises import EXERCISES

    rows = [[e["name"], e["category"], e["exercise"]] for e in EXERCISES]
    return JSONResponse(rows, headers={"Cache-Control": "public, max-age=604800"})


@app.post("/api/sync", dependencies=[Depends(require_key)])
def sync(body: SyncRequest) -> dict:
    if body.start > body.end:
        raise HTTPException(status_code=422, detail="bad_window")
    if not sync_lock.acquire(timeout=60):
        raise HTTPException(status_code=429, detail="sync_running")
    try:
        try:
            api = account.client()
            result = Syncer(store, api).sync(body.workouts, body.start, body.end)
        except Exception as e:
            raise _garmin_error(e) from None
        finally:
            account.persist()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _record_meta(lastSyncAt=now, lastSyncErrors=len(result["errors"]))
        return {"syncedAt": now, **result}
    finally:
        sync_lock.release()


# -- test copy of the app (served from the same address) ------------------------
if APP_DIR and Path(APP_DIR, "index.html").exists():
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(Path(APP_DIR, "index.html"), headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=APP_DIR), name="app")
