"""Offline tests: no Garmin account or network needed.

Run from the helper folder:  APP_KEY=... python -m pytest -q
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from convert import convert, fingerprint  # noqa: E402
from store import LocalStore  # noqa: E402
from sync import Syncer  # noqa: E402

KEY = "k" * 40


def squat(sets=4, reps=6, kg=80, rest=180):
    return {"title": "Back Squat", "category": "strength", "measurement": "weight_reps",
            "garmin": {"category": "SQUAT", "exercise": "BARBELL_BACK_SQUAT"},
            "plannedSets": [{"reps": reps, "weightKg": kg, "restSec": rest} for _ in range(sets)]}


def run_entry(**plan):
    return {"title": "Easy Run", "category": "run", "measurement": "distance_time", "plannedCardio": plan}


def workout(wid="w1", date="2026-10-10", entries=None, name="Lower A", status="planned"):
    return {"id": wid, "date": date, "name": name, "status": status, "notes": "", "entries": entries or [squat()]}


def walk(steps):
    for s in steps:
        yield s
        yield from walk(s.get("workoutSteps", []))


# -- convert -----------------------------------------------------------------
def test_identical_sets_become_one_repeat_group():
    parts = convert(workout())
    assert list(parts) == ["gym"]
    steps = parts["gym"]["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 1 and steps[0]["type"] == "RepeatGroupDTO" and steps[0]["numberOfIterations"] == 4
    work, rest = steps[0]["workoutSteps"]
    assert work["category"] == "SQUAT" and work["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert work["endConditionValue"] == 6 and work["weightValue"] == 80000
    assert rest["endConditionValue"] == 180
    assert work["description"].startswith("Back Squat")


def test_step_orders_unique_and_increasing():
    w = workout(entries=[squat(), squat(sets=3, reps=5, kg=100)])
    w["entries"][1]["plannedSets"][2]["reps"] = 3  # a top set makes them differ
    orders = [s["stepOrder"] for s in walk(convert(w)["gym"]["workoutSegments"][0]["workoutSteps"])]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


def test_unmatched_exercise_uses_fallback_category_and_keeps_title():
    e = squat()
    e["garmin"] = None
    e["title"] = "Landmine thing"
    work = convert(workout(entries=[e]))["gym"]["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work["category"] == "TOTAL_BODY" and work["description"].startswith("Landmine thing")


def test_timed_strength_sets():
    e = {"title": "Plank", "category": "strength", "measurement": "time", "garmin": {"category": "PLANK", "exercise": "PLANK"},
         "plannedSets": [{"durationSec": 60, "restSec": 60}] * 3}
    work = convert(workout(entries=[e]))["gym"]["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work["endCondition"]["conditionTypeKey"] == "time" and work["endConditionValue"] == 60


def test_distance_run_with_pace_range():
    parts = convert(workout(entries=[run_entry(distanceM=8000, paceSecPerKm=330)]))
    step = parts["run"]["workoutSegments"][0]["workoutSteps"][0]
    assert parts["run"]["sportType"]["sportTypeKey"] == "running"
    assert step["endCondition"]["conditionTypeKey"] == "distance" and step["endConditionValue"] == 8000
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert step["targetValueOne"] < step["targetValueTwo"]  # slower pace is the lower speed


def test_interval_run():
    e = run_entry(distanceM=8000, intervals={"reps": 6, "workM": 800, "paceSecPerKm": 230, "recSec": 90})
    steps = convert(workout(entries=[e]))["run"]["workoutSegments"][0]["workoutSteps"]
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
    rep = steps[1]
    assert rep["numberOfIterations"] == 6
    assert rep["workoutSteps"][0]["endConditionValue"] == 800 and rep["workoutSteps"][1]["endConditionValue"] == 90


def test_hybrid_workout_splits_into_two_named_parts():
    parts = convert(workout(entries=[squat(), run_entry(distanceM=3000)], name="Hybrid"))
    assert set(parts) == {"gym", "run"}
    assert parts["gym"]["workoutName"] == "Hybrid · gym" and parts["run"]["workoutName"] == "Hybrid · run"


def test_mobility_alone_is_a_cardio_workout():
    e = {"title": "Mobility Flow", "category": "other", "measurement": "time", "plannedCardio": {"durationSec": 1200}}
    parts = convert(workout(entries=[e]))
    assert list(parts) == ["other"] and parts["other"]["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] == 1200


def test_empty_workout_sends_nothing():
    assert convert(workout(entries=[])) == {} or convert({"id": "x", "date": "2026-10-10", "entries": []}) == {}


# -- sync ----------------------------------------------------------------------
class FakeGarmin:
    def __init__(self):
        self.workouts, self.schedules, self.next = {}, {}, 100

    def upload_workout(self, body):
        self.next += 1
        self.workouts[self.next] = body
        return {"workoutId": self.next}

    def update_workout(self, wid, body):
        if wid not in self.workouts:
            raise RuntimeError("404")
        self.workouts[wid] = body
        return {}

    def delete_workout(self, wid):
        self.workouts.pop(wid, None)

    def schedule_workout(self, wid, date):
        self.next += 1
        self.schedules[self.next] = (wid, date)
        return {"workoutScheduleId": self.next}

    def unschedule_workout(self, sid):
        self.schedules.pop(sid, None)


@pytest.fixture
def env(tmp_path):
    return LocalStore(str(tmp_path)), FakeGarmin()


def dates(g):
    return sorted(d for _, d in g.schedules.values())


def test_sync_creates_then_is_idempotent(env):
    store, g = env
    r = Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    assert r["created"] == 1 and len(g.workouts) == 1 and dates(g) == ["2026-10-10"]
    r = Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    assert r["unchanged"] == 1 and len(g.workouts) == 1 and len(g.schedules) == 1


def test_sync_moves_updates_and_removes(env):
    store, g = env
    Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    r = Syncer(store, g).sync([workout(date="2026-10-12", entries=[squat(kg=85)])], "2026-10-08", "2026-11-05")
    assert r["updated"] == 1 and dates(g) == ["2026-10-12"]
    assert list(g.workouts.values())[0]["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]["weightValue"] == 85000
    r = Syncer(store, g).sync([], "2026-10-08", "2026-11-05")
    assert r["removed"] == 1 and not g.workouts and not g.schedules


def test_done_kept_skipped_removed(env):
    store, g = env
    Syncer(store, g).sync([workout("a"), workout("b", date="2026-10-11")], "2026-10-08", "2026-11-05")
    Syncer(store, g).sync([workout("a", status="done"), workout("b", status="skipped")], "2026-10-08", "2026-11-05")
    assert dates(g) == ["2026-10-10"]


def test_workout_deleted_by_hand_in_garmin_is_recreated(env):
    store, g = env
    Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    g.workouts.clear()
    Syncer(store, g).sync([workout(entries=[squat(kg=90)])], "2026-10-08", "2026-11-05")
    assert len(g.workouts) == 1


def test_past_workouts_are_left_alone(env):
    store, g = env
    Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    Syncer(store, g).sync([], "2026-10-20", "2026-11-17")  # window moved on
    assert len(g.workouts) == 1


def test_other_workouts_in_garmin_are_never_touched(env):
    store, g = env
    g.workouts[1] = {"workoutName": "My own Garmin workout"}
    Syncer(store, g).sync([workout()], "2026-10-08", "2026-11-05")
    Syncer(store, g).remove_all()
    assert list(g.workouts) == [1]


# -- API -------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APP_KEY", KEY)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "data"))
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://andreapagnini.github.io")
    import main

    main = importlib.reload(main)
    return TestClient(main.app), main


def test_api_requires_app_key(client):
    c, _ = client
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/status", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200 and r.json()["connected"] is False


def test_api_sync_without_garmin_says_not_connected(client):
    c, _ = client
    r = c.post("/api/sync", json={"start": "2026-10-08", "end": "2026-11-05", "workouts": []},
               headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 409 and r.json()["detail"] == "not_connected"


def test_api_cors_only_for_the_app(client):
    c, _ = client
    ok = c.options("/api/status", headers={"Origin": "https://andreapagnini.github.io", "Access-Control-Request-Method": "GET"})
    bad = c.options("/api/status", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://andreapagnini.github.io"
    assert "access-control-allow-origin" not in bad.headers


def test_api_exercise_catalog(client):
    c, _ = client
    rows = c.get("/api/exercises").json()
    assert len(rows) > 1000 and ["Barbell Back Squat", "SQUAT", "BARBELL_BACK_SQUAT"] in rows


def test_api_login_with_mfa_and_sync(client, monkeypatch):
    c, main = client
    fake = FakeGarmin()

    class FakeClient:
        def __init__(self, outer):
            self.outer = outer
            self._tokenstore_path = None

        def dump(self, path):
            p = Path(path) / "garmin_tokens.json"
            p.write_text('{"di_token":"t","di_refresh_token":"r","di_client_id":"c"}')

    class FakeAccount:
        def __init__(self, **kw):
            self.kw = kw
            self.client = FakeClient(self)
            self.full_name = "Andrea"

        def login(self, tokenstore=None):
            return ("needs_mfa", None) if self.kw.get("return_on_mfa") else (None, None)

        def resume_login(self, state, code):
            assert code == "123456"

        def __getattr__(self, name):
            return getattr(fake, name)

    main.account.factory = FakeAccount
    h = {"Authorization": f"Bearer {KEY}"}
    assert c.post("/api/garmin/login", json={"email": "a@b.c", "password": "x"}, headers=h).json() == {"state": "mfa"}
    assert c.post("/api/garmin/mfa", json={"code": "123456"}, headers=h).json() == {"state": "connected"}
    assert c.get("/api/status", headers=h).json()["connected"] is True
    main.account.forget_client()  # next call restores from the stored sign-in
    r = c.post("/api/sync", json={"start": "2026-10-08", "end": "2026-11-05", "workouts": [workout()]}, headers=h).json()
    assert r["created"] == 1 and r["errors"] == []
    r = c.post("/api/garmin/disconnect", json={"removeWorkouts": True}, headers=h).json()
    assert r["removed"] == 1 and not fake.workouts
    assert c.get("/api/status", headers=h).json()["connected"] is False


def test_fingerprint_stable():
    assert fingerprint(convert(workout())["gym"]) == fingerprint(convert(workout())["gym"])
