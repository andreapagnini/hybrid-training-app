# Private Garmin helper

Small web service that sends the app's planned workouts to the owner's Garmin
Connect calendar, one way only. It uses
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
(MIT licence, © Ron Klinkien), an unofficial Garmin Connect client, so it is for
the owner's own account only. Plan and trade-offs: `plan/garmin-connect-plan.md`
and `plan/garmin-implementation-plan.md` in the project files.

```
app (garmin.js) ──HTTPS + app key──► helper (Cloud Run) ──► Garmin Connect ──► watch
```

## Files

| File | What it does |
|---|---|
| `main.py` | HTTP API (FastAPI). Every `/api/*` call except `/api/health` and `/api/exercises` needs `Authorization: Bearer <APP_KEY>`. Also serves a test copy of the app from `APP_DIR`. |
| `account.py` | Garmin sign-in: login, two-step code, restore, disconnect. The password is never stored; the token file is. |
| `convert.py` | App workout → Garmin workout JSON (strength sets, timed sets, runs with pace range, intervals, mobility). |
| `sync.py` | Makes the Garmin calendar match the app for a date window. Only touches workouts it created (`synced.json`). |
| `store.py` | Where the two files live: `gs://bucket` on Cloud Run, a local folder otherwise. |
| `deploy.sh` | One-command deploy to Google Cloud Run, run from Cloud Shell. |
| `login_locally.py` | Fallback sign-in from a laptop if Garmin refuses sign-ins from the cloud. |

## API

| Call | Body | Answer |
|---|---|---|
| `GET /api/health` | – | `{ok, version}` |
| `GET /api/status` | – | `{connected, garminName, lastSyncAt, …}` |
| `POST /api/garmin/login` | `{email, password}` | `{state: "connected" \| "mfa"}` |
| `POST /api/garmin/mfa` | `{code}` | `{state: "connected"}` |
| `POST /api/garmin/tokens` | `{tokens}` | fallback import of a laptop sign-in |
| `POST /api/garmin/disconnect` | `{removeWorkouts}` | forgets the sign-in, optionally deletes what it sent |
| `GET /api/exercises` | – | Garmin's strength catalog `[[name, category, exercise], …]` |
| `POST /api/sync` | `{start, end, workouts: [...]}` | `{syncedAt, created, updated, removed, unchanged, errors}` |

Errors come back as `{"detail": code}` with codes `wrong_app_key`, `not_connected`,
`garmin_sign_in_failed`, `garmin_busy`, `garmin_error`, `sync_running`. The app turns them into plain sentences.

### Sync payload

Neutral on purpose, so another connection type (intervals.icu, Garmin's official API) can take the same input:

```json
{"id": "w1", "date": "2026-10-10", "status": "planned", "name": "Lower A", "notes": "",
 "entries": [{"title": "Back Squat", "category": "strength", "measurement": "weight_reps", "perSide": false,
              "notes": "first line of the exercise notes",
              "garmin": {"category": "SQUAT", "exercise": "BARBELL_BACK_SQUAT"},
              "plannedSets": [{"reps": 6, "weightKg": 80, "restSec": 180}], "plannedCardio": null}]}
```

Rules: `planned` workouts in the window are created or updated; `done` and `partial` are left alone;
`skipped`, deleted or moved-out workouts are removed from Garmin. A workout mixing gym and running
becomes two Garmin workouts ("Name · gym", "Name · run").

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
APP_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))') .venv/bin/python -m pytest -q tests
APP_KEY=... STORE_URL=./data .venv/bin/uvicorn main:app --port 8080
```

Tests run offline with a fake Garmin; nothing touches a real account.

## Deploy / update

In Google Cloud Shell, from the repository root on this branch:

```bash
bash helper/deploy.sh PROJECT_ID
```

It creates (once) a private bucket, a service account that can only use that bucket, and the app key in
Secret Manager, then builds and deploys the service in `europe-west1`, max 1 instance, scale to zero.
It prints the setup code to paste in the app (Settings › Garmin). Run it again to update.

## Updating python-garminconnect

The version is pinned in `requirements.txt`. To update: read the release notes and the diff of
`garminconnect/__init__.py` and `client.py` (it must still only talk to Garmin hosts), bump the pin,
run the tests, deploy.
