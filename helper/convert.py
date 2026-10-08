"""Turn one planned app workout into Garmin Connect workout JSON.

Input is the neutral workout the app sends (see README "Sync payload").
Output is a list of parts, one per Garmin sport: a gym part (strength
training), a run part (running) and an "other" part (cardio, only when the
workout has no gym part). Most workouts produce exactly one part.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from garminconnect.workout import (
    ConditionType,
    SportType,
    StepType,
    TargetType,
)

SPORTS = {
    "gym": {"sportTypeId": SportType.STRENGTH_TRAINING, "sportTypeKey": "strength_training", "displayOrder": 5},
    "run": {"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
    "other": {"sportTypeId": SportType.CARDIO_TRAINING, "sportTypeKey": "cardio_training", "displayOrder": 6},
}
PART_LABEL = {"gym": "gym", "run": "run", "other": "mobility"}

# Exercises with no Garmin match still need a valid category; the app's own
# title goes in the step note.
FALLBACK_CATEGORY = "TOTAL_BODY"
PACE_TOLERANCE_SEC = 10  # pace range of +/- 10 s/km around the target
NOTE_MAX = 250
NAME_MAX = 80
DESCRIPTION_MAX = 1000

_NO_TARGET = {"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
_PACE_TARGET = {"workoutTargetTypeId": TargetType.PACE_ZONE, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}

_STEP_TYPES = {
    "warmup": {"stepTypeId": StepType.WARMUP, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": StepType.COOLDOWN, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": StepType.REST, "stepTypeKey": "rest", "displayOrder": 5},
    "repeat": {"stepTypeId": StepType.REPEAT, "stepTypeKey": "repeat", "displayOrder": 6},
}
_CONDITIONS = {
    "lap": {"conditionTypeId": ConditionType.LAP_BUTTON, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
    "time": {"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
    "distance": {"conditionTypeId": ConditionType.DISTANCE, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True},
    "iterations": {"conditionTypeId": ConditionType.ITERATIONS, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
    "reps": {"conditionTypeId": ConditionType.REPS, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True},
}
_KG = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


class _Steps:
    """Builds steps with the unique, increasing stepOrder Garmin requires."""

    def __init__(self) -> None:
        self.order = 0
        self.seconds = 0.0

    def _next(self) -> int:
        self.order += 1
        return self.order

    def step(self, kind: str, cond: str, value: float | None = None, note: str = "", target: dict | None = None, **extra: Any) -> dict:
        s: dict[str, Any] = {
            "type": "ExecutableStepDTO",
            "stepOrder": self._next(),
            "stepType": dict(_STEP_TYPES[kind]),
            "endCondition": dict(_CONDITIONS[cond]),
            "targetType": dict(target or _NO_TARGET),
        }
        if value is not None:
            s["endConditionValue"] = float(value)
        if note:
            s["description"] = note[:NOTE_MAX]
        s.update(extra)
        return s

    def repeat(self, times: int, build) -> dict:
        group = {
            "type": "RepeatGroupDTO",
            "stepOrder": self._next(),
            "stepType": dict(_STEP_TYPES["repeat"]),
            "numberOfIterations": int(times),
            "endCondition": dict(_CONDITIONS["iterations"]),
            "endConditionValue": float(times),
            "smartRepeat": False,
            "workoutSteps": [],
        }
        before = self.seconds
        group["workoutSteps"] = build()
        self.seconds = before + (self.seconds - before) * times
        return group


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _pace_target(pace_sec_per_km: float | None) -> dict[str, Any]:
    """Pace range in m/s; Garmin wants the slower pace as the lower limit."""
    if not pace_sec_per_km:
        return {}
    slow = pace_sec_per_km + PACE_TOLERANCE_SEC
    fast = max(pace_sec_per_km - PACE_TOLERANCE_SEC, 60)
    return {
        "target": _PACE_TARGET,
        "targetValueOne": round(1000 / slow, 4),
        "targetValueTwo": round(1000 / fast, 4),
    }


def _entry_note(entry: dict) -> str:
    """Your own exercise name first, so it shows even when Garmin's differs."""
    parts = []
    title = (entry.get("title") or "").strip()
    if title:
        parts.append(title)
    if entry.get("perSide"):
        parts.append("reps and kg per side")
    notes = (entry.get("notes") or "").strip()
    if notes:
        parts.append(notes.splitlines()[0])
    return " · ".join(parts)


def _strength_steps(entry: dict, b: _Steps) -> list[dict]:
    garmin = entry.get("garmin") or {}
    category = garmin.get("category") or FALLBACK_CATEGORY
    exercise = garmin.get("exercise") or ""
    note = _entry_note(entry)
    sets = [s for s in (entry.get("plannedSets") or []) if isinstance(s, dict)]
    if not sets:
        return []
    timed = entry.get("measurement") == "time"
    weighted = entry.get("measurement") == "weight_reps"

    def work(s: dict) -> dict:
        extra: dict[str, Any] = {"category": category, "exerciseName": exercise}
        kg = _num(s.get("weightKg")) if weighted else None
        if kg:
            extra["weightValue"] = kg * 1000.0
            extra["weightUnit"] = dict(_KG)
        if timed:
            dur = _num(s.get("durationSec")) or 30
            b.seconds += dur
            return b.step("interval", "time", dur, note, **extra)
        reps = _num(s.get("reps"))
        if reps:
            b.seconds += reps * 4
            return b.step("interval", "reps", int(reps), note, **extra)
        return b.step("interval", "lap", None, note, **extra)

    def rest(s: dict) -> list[dict]:
        r = _num(s.get("restSec"))
        if not r:
            return []
        b.seconds += r
        return [b.step("rest", "time", r)]

    key = lambda s: (s.get("reps"), s.get("weightKg"), s.get("durationSec"), s.get("restSec"))  # noqa: E731
    if len(sets) > 1 and all(key(s) == key(sets[0]) for s in sets):
        return [b.repeat(len(sets), lambda: [work(sets[0]), *rest(sets[0])])]
    out: list[dict] = []
    for s in sets:
        out.append(work(s))
        out.extend(rest(s))
    return out


def _run_steps(entry: dict, b: _Steps) -> list[dict]:
    plan = entry.get("plannedCardio") or {}
    note = _entry_note(entry)
    iv = plan.get("intervals")
    if isinstance(iv, dict) and _num(iv.get("reps")) and _num(iv.get("workM")):
        out = [b.step("warmup", "lap", None, "Warm-up · press lap to start the repeats")]
        pace = _num(iv.get("paceSecPerKm"))
        work_m = _num(iv["workM"])
        rec = _num(iv.get("recSec"))

        def one() -> list[dict]:
            t = _pace_target(pace)
            if pace:
                b.seconds += work_m / 1000 * pace
            steps = [b.step("interval", "distance", work_m, note, t.get("target"),
                            **{k: v for k, v in t.items() if k != "target"})]
            if rec:
                b.seconds += rec
                steps.append(b.step("recovery", "time", rec))
            else:
                steps.append(b.step("recovery", "lap", None))
            return steps

        out.append(b.repeat(int(_num(iv["reps"])), one))
        out.append(b.step("cooldown", "lap", None, "Cool-down · press lap to finish"))
        return out

    dist = _num(plan.get("distanceM"))
    dur = _num(plan.get("durationSec"))
    pace = _num(plan.get("paceSecPerKm"))
    t = _pace_target(pace)
    extra = {k: v for k, v in t.items() if k != "target"}
    if dist:
        b.seconds += (dist / 1000 * pace) if pace else (dur or 0)
        return [b.step("interval", "distance", dist, note, t.get("target"), **extra)]
    if dur:
        b.seconds += dur
        return [b.step("interval", "time", dur, note, t.get("target"), **extra)]
    return [b.step("interval", "lap", None, note)]


def _other_steps(entry: dict, b: _Steps) -> list[dict]:
    plan = entry.get("plannedCardio") or {}
    note = _entry_note(entry)
    dur = _num(plan.get("durationSec"))
    sets = entry.get("plannedSets") or []
    if not dur and sets:
        dur = sum(_num(s.get("durationSec")) or 0 for s in sets if isinstance(s, dict)) or None
    if dur:
        b.seconds += dur
        return [b.step("interval", "time", dur, note)]
    return [b.step("interval", "lap", None, note)]


def _part_of(entry: dict) -> str:
    cat = entry.get("category")
    if cat == "run":
        return "run"
    if cat == "strength":
        return "gym"
    return "other"


def convert(workout: dict) -> dict[str, dict]:
    """Return {part_key: garmin_workout_json} for one app workout."""
    entries = [e for e in (workout.get("entries") or []) if isinstance(e, dict)]
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(_part_of(e), []).append(e)
    # Mobility or other entries ride along in a gym session when there is one.
    if "gym" in groups and "other" in groups:
        groups["gym"].extend(groups.pop("other"))
    if not groups:
        return {}

    name = (workout.get("name") or "Workout").strip()[:NAME_MAX]
    description = (workout.get("notes") or "").strip()[:DESCRIPTION_MAX]
    parts: dict[str, dict] = {}
    for key in ("gym", "run", "other"):
        if key not in groups:
            continue
        b = _Steps()
        steps: list[dict] = []
        for e in groups[key]:
            if key == "run":
                steps.extend(_run_steps(e, b))
            elif _part_of(e) == "gym":
                steps.extend(_strength_steps(e, b))
            else:
                steps.extend(_other_steps(e, b))
        if not steps:
            continue
        part_name = name if len(groups) == 1 else f"{name} · {PART_LABEL[key]}"
        body: dict[str, Any] = {
            "workoutName": part_name[:NAME_MAX],
            "sportType": dict(SPORTS[key]),
            "estimatedDurationInSecs": int(b.seconds),
            "workoutSegments": [{"segmentOrder": 1, "sportType": dict(SPORTS[key]), "workoutSteps": steps}],
        }
        if description:
            body["description"] = description
        parts[key] = body
    return parts


def fingerprint(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
