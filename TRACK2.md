# Track 2 — Baseline App + Memory (APP-01 … APP-11)

Native app (Next.js + Astryx) + FastAPI gateway + intake graph + long-term memory. Talks to Tracks 1 and 3 only
through MongoDB (MASTERPLAN §6).

## Run it (WSL / Linux)

```bash
uv venv --python 3.12 && uv sync --group dev        # Python 3.12 deps
(cd web && npm install)                             # Node 22+; postinstall copies the MapLibre worker
cp .env.example .env                                # fill MONGODB_URI, LLM_*, VOYAGE_API_KEY (+ ELEVENLABS_*)
.venv/bin/python scripts/check_env.py               # must be green
.venv/bin/python scripts/setup_db.py                # validators, indexes, time-series, search indexes (idempotent)
.venv/bin/python scripts/load_fixtures.py --city lisbon   # fixtures pack (+ cities for autocomplete)
scripts/dev.sh up                                   # gateway :8000 + web :3000 (logs in .run/), `down` to stop
```

**Solo demo without Track 3:** use your own DB and the dev stub planner, which fakes research progress and
writes a fixture board (it refuses to run on the shared `wander` DB):

```bash
export MONGODB_DB=wander_dev
.venv/bin/python scripts/setup_db.py && .venv/bin/python scripts/load_fixtures.py --city lisbon
scripts/dev.sh up --stub
```

Voice: `python scripts/setup_voice_agent.py --write-env` creates/updates the **Wander** ElevenLabs agent (prompt +
2 client tools, voice = `ELEVENLABS_VOICE_ID`) and writes its id to `.env`.

## Tests

```bash
.venv/bin/pytest -q                                          # offline unit checks (slots, auth, memory note)
MONGODB_DB=wander_dev scripts/dev.sh up --stub
MONGODB_DB=wander_dev .venv/bin/python scripts/e2e_track2.py  # 36 live checks against the ticket ACs
MONGODB_DB=wander_dev .venv/bin/python scripts/voice_smoke.py # real ElevenLabs agent, typed turns, client tools
```

## What's where

| Ticket | Where |
|---|---|
| APP-01 Atlas setup, fail-fast, schemas, indexes | `shared/config.py`, `shared/schemas/*.json`, `scripts/setup_db.py`, `scripts/check_env.py`, `infra/indexes/` |
| APP-02 Fixtures | `scripts/make_fixtures.py` → `fixtures/lisbon/*.json`, `fixtures/cities.json`, `scripts/load_fixtures.py` |
| APP-03 Gateway + Mongo auth | `track2_app/gateway/app.py`, `track2_app/auth.py` (scrypt + `auth_sessions` TTL, httpOnly cookie) |
| APP-04 Intake graph | `track2_app/intake/` (LangGraph + `MongoDBSaver`, `interrupt()` per question batch) |
| APP-05 Native app | `web/app/page.tsx`, `web/app/trip/[sid]`, `web/components/wander/QuestionCard.tsx` |
| APP-06 Live board | `web/app/b/[slug]`, SSE `GET /api/boards/{slug}/events` (MongoDB change stream) |
| APP-07 Voice intake | `web/components/wander/VoiceIntake.tsx`, `POST /api/voice/session`, `scripts/setup_voice_agent.py` |
| APP-08 Memory | `track2_app/memory/store.py` (`memories` + Voyage + `$vectorSearch`), `web/app/memory` |
| APP-09 Autocomplete | `GET /api/autocomplete` (Atlas Search `cities_autocomplete`, regex fallback) |
| APP-10 Metering | `track2_app/usage.py` (`usage` per user/day, `WANDER_DAILY_LLM_QUOTA`, 429 upsell) |
| APP-11 Export | `GET /api/boards/{slug}/ics`, copy-as-text, print |

## Contracts other tracks rely on

- **→ Track 3:** intake ends with `trips.status = "ready_for_research"` and a filled `trips.profile/dates/travelers/legs/origin`.
  User changes are inserted as `refine_requests {source: "user", status: "queued"}`.
- **← Track 3:** the board page renders any `boards` doc by `share_slug`; slot `item.place_id` geo is looked up in
  `places` if the item has none; `link_ids`/`bookings[].link_id` resolve against `links` (string or ObjectId ids).
  A board appearing for the trip flips the session to `ready`.
- **← Track 1:** board-channel `alerts` for the trip render live in `DisruptionBanner` (Track 1 owns that component).
  Disruption choices written as `memories {kind: "feedback", source: "disruption_choice"}` are recalled at intake.

## Notes

- Memory lives in the `memories` collection (prefs = key/value, episodes/feedback = text + Voyage embedding).
  Embedding is best-effort: without a Voyage payment method the free tier is 3 requests/min, so memories save
  without vectors and recall falls back to recency until billing is added.
- UI is Astryx-only (`npx astryx doctor --built web` → 0 failures; the one warning is MapLibre's own stylesheet).
