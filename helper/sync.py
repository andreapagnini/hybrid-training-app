"""Make the Garmin calendar match the app's planned workouts in a date window.

The helper keeps a record (synced.json) of every Garmin workout it created:
  { app_workout_id: { "date": "YYYY-MM-DD", "name": str,
                      "parts": { part: {"workoutId": int, "scheduleId": int|None, "hash": str} } } }
Only workouts in that record are ever changed or deleted, so anything you
created yourself in Garmin Connect is left alone.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
from typing import Any

from convert import convert, fingerprint
from store import Store

RECORD = "synced.json"
KEEP_PAST_DAYS = 60
log = logging.getLogger("helper.sync")


def _schedule_id(resp: Any) -> int | None:
    if isinstance(resp, dict):
        for key in ("workoutScheduleId", "scheduleId", "id"):
            v = resp.get(key)
            if isinstance(v, int):
                return v
    return None


def _workout_id(resp: Any) -> int:
    if isinstance(resp, dict) and isinstance(resp.get("workoutId"), int):
        return resp["workoutId"]
    raise RuntimeError("Garmin did not return a workout id")


class Syncer:
    def __init__(self, store: Store, api: Any) -> None:
        self.store = store
        self.api = api
        self.record: dict[str, dict] = store.get_json(RECORD, {})

    def _save(self) -> None:
        self.store.put_json(RECORD, self.record)

    # -- Garmin operations ---------------------------------------------------
    def _create(self, body: dict, date: str) -> dict:
        wid = _workout_id(self.api.upload_workout(body))
        sid = _schedule_id(self.api.schedule_workout(wid, date))
        return {"workoutId": wid, "scheduleId": sid, "hash": fingerprint(body)}

    def _remove(self, part: dict) -> None:
        if part.get("scheduleId"):
            with contextlib.suppress(Exception):
                self.api.unschedule_workout(part["scheduleId"])
        with contextlib.suppress(Exception):
            self.api.delete_workout(part["workoutId"])

    def _reschedule(self, part: dict, date: str) -> None:
        if part.get("scheduleId"):
            with contextlib.suppress(Exception):
                self.api.unschedule_workout(part["scheduleId"])
        part["scheduleId"] = _schedule_id(self.api.schedule_workout(part["workoutId"], date))

    # -- one workout -----------------------------------------------------------
    def _apply(self, w: dict, stats: dict) -> None:
        wid, date = str(w["id"]), w["date"]
        bodies = convert(w)
        rec = self.record.get(wid, {"date": date, "parts": {}})
        old_parts: dict = rec.get("parts", {})
        new_parts: dict = {}
        changed = False
        for key, body in bodies.items():
            part = old_parts.get(key)
            h = fingerprint(body)
            if part is None:
                new_parts[key] = self._create(body, date)
                changed = True
                continue
            if part.get("hash") != h:
                try:
                    self.api.update_workout(part["workoutId"], body)
                    part["hash"] = h
                except Exception as e:  # deleted by hand in Garmin, or rejected
                    log.info("update failed (%s), recreating", type(e).__name__)
                    self._remove(part)
                    part = self._create(body, date)
                    new_parts[key] = part
                    changed = True
                    continue
                changed = True
            if rec.get("date") != date:
                self._reschedule(part, date)
                changed = True
            new_parts[key] = part
        for key, part in old_parts.items():
            if key not in new_parts:
                self._remove(part)
                changed = True
        if new_parts:
            self.record[wid] = {"date": date, "name": w.get("name") or "", "parts": new_parts}
        else:
            self.record.pop(wid, None)
        if changed:
            stats["created" if not old_parts else "updated"] += 1
        else:
            stats["unchanged"] += 1

    # -- whole window ----------------------------------------------------------
    def sync(self, workouts: list[dict], start: str, end: str) -> dict:
        stats: dict[str, Any] = {"created": 0, "updated": 0, "removed": 0, "unchanged": 0, "errors": []}
        in_window = [w for w in workouts if start <= str(w.get("date", "")) <= end]
        # Planned workouts are sent. Done or partial ones stay in Garmin as they
        # are; skipped or deleted ones are taken off the Garmin calendar.
        wanted = {str(w["id"]): w for w in in_window if w.get("status", "planned") == "planned"}
        keep = {str(w["id"]) for w in in_window if w.get("status") in ("done", "partial")}
        try:
            for wid, w in wanted.items():
                try:
                    self._apply(w, stats)
                except Exception as e:
                    log.warning("workout failed: %s", type(e).__name__)
                    stats["errors"].append({"id": wid, "name": w.get("name") or "", "message": _short(e)})
                finally:
                    self._save()
            for wid, rec in list(self.record.items()):
                if wid in wanted or wid in keep or not (start <= rec.get("date", "") <= end):
                    continue
                for part in rec.get("parts", {}).values():
                    self._remove(part)
                self.record.pop(wid, None)
                stats["removed"] += 1
                self._save()
            self._prune(start)
        finally:
            self._save()
        return stats

    def remove_all(self) -> int:
        n = 0
        for wid, rec in list(self.record.items()):
            for part in rec.get("parts", {}).values():
                self._remove(part)
            self.record.pop(wid, None)
            n += 1
        self._save()
        return n

    def _prune(self, start: str) -> None:
        """Forget (but don't delete in Garmin) workouts long in the past."""
        try:
            cutoff = (dt.date.fromisoformat(start) - dt.timedelta(days=KEEP_PAST_DAYS)).isoformat()
        except ValueError:
            return
        for wid, rec in list(self.record.items()):
            if rec.get("date", "") < cutoff:
                self.record.pop(wid, None)


def _short(e: Exception) -> str:
    text = str(e).strip() or type(e).__name__
    return text.splitlines()[0][:200]
