# Wander — disruption management

Track 1 (`D-01`–`D-11`) on branch `shiv`: watches → sourced signals → scoped impact → response options → board/email/voice alerts → confirmed fix → transactional refine handoff + memory feedback. Full architecture: [MASTERPLAN.md](MASTERPLAN.md).

## Setup (PowerShell)

Requires Python 3.12+, `uv`, Node 20.9+, and MongoDB Atlas. Fixtures use Atlas too; there is no local database fallback.

```powershell
uv --cache-dir .uv-cache sync
# Only if .env does not already exist: Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts/check_env.py --config-only
.\.venv\Scripts\python.exe scripts/setup_db.py
.\.venv\Scripts\python.exe scripts/load_fixtures.py --city lisbon
.\.venv\Scripts\python.exe scripts/check_env.py --strict --providers
```

Fill the required block in `.env.example`. `.env` files remain ignored. Setup creates Track 1-owned validators, indexes, time-series `signals` (14-day TTL) and `price_snapshots`; it does not replace other tracks' schemas. Fixtures upsert known synthetic IDs and refuse to overwrite non-fixture data. Avoid reseeding while someone is confirming choices.

## Run the demo

Terminal 1:

```powershell
.\.venv\Scripts\python.exe -m uvicorn track1_disruption.api:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
cd web
npm.cmd ci --cache .npm-cache
npm.cmd run dev
```

Open **http://127.0.0.1:3000/b/lisbon-fixture-demo** and choose **Continue as demo traveler**. Trigger a delay in terminal 3:

```powershell
.\.venv\Scripts\python.exe scripts/simulate_disruption.py --trip trip_lisbon --type flight_delay --minutes 180
```

The board receives an SSE update. Play the briefing and choose **Shift arrival-day plans**. The resolution transaction writes a disruption resolution, refine request, audit and memory feedback. The fixture consumer changes only scoped slots and increments the board version; double resolution is rejected.

Simulator types: `flight_delay`, `flight_cancel`, `weather`, `strike`, `closure`, `event_cancel`, `dead_link`, `price_spike`. Identical observations deduplicate; change `--minutes` to create another delay. Fixture dates intentionally match the master plan's March 2026 examples and are labeled as simulated, not current travel reports. The disruption-only page is `/trip/trip_lisbon/disruptions` after signing in.

## Real email and voice

Default fixture delivery records the email and plays the included demo MP3 without sending an email. For actual delivery:

```powershell
.\.venv\Scripts\python.exe scripts/simulate_disruption.py --type flight_delay --minutes 240 --live-alerts
```

Set `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `DISRUPTION_TEST_EMAIL`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_TTS_MODEL`. The voice can be read from an existing ElevenLabs agent's `conversation_config.tts.voice_id`. Resend send-only keys work; lack of domain-list permission does not invalidate them. `onboarding@resend.dev` is the test sender; use a verified domain for real travelers.

Emails link to the board to review options; visiting a link never changes the plan. Mutations require the trip owner's session. Failures remain pending for retry; email idempotency keys avoid duplicate sends. Audio is stored under ignored `media/voice/`. `scripts/record_fixture_voice.py` regenerates the reusable demo briefing.

## Workers, providers and integration

```powershell
.\.venv\Scripts\python.exe -m track1_disruption.run
# Or a bounded sweep:
.\.venv\Scripts\python.exe -m track1_disruption.run --once
.\.venv\Scripts\python.exe scripts/pretrip_digest.py --fixture
```

Add `--live-alerts` for actual email/TTS delivery. The scheduler has atomic watch leases, stale recovery, backoff, an eight-thread pool, board change streams and startup sweeps. Unique signal receipts deduplicate before mirroring to time-series storage. LangGraph persists with `MongoDBSaver`, tags `track:1`, and interrupts for user choice; committed handoffs are recoverable. Impact uses exact leg/date matches, `$geoWithin`, and aggregation over bookings at risk. Replacements use `$geoNear`, consensus and Voyage reranking.

In seeded/live modes, adapters use Open-Meteo, search plus structured LLM extraction for area/venue news, and Ticketmaster when configured. HTTP calls use a shared Mongo cache and bounded retries/timeouts. Link validation updates `links.validation`; price monitoring consumes timestamped `links.price_hint` values against a seven-day `$setWindowFields` average. T-48h digests include weather, transport notices and outstanding timed tickets.

**Flights use the simulator in this setup, as requested.** Flight provider credentials are optional; AviationStack/AeroAPI adapters are available if added. `TICKETMASTER_API_KEY` enables live event polling. LangSmith uses `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=wander`; tracing remains disabled until configured.

Track 1 imports no other track. Track 2 owns auth/intake and Track 3 owns real replanning. `track1_disruption.api.router` can be mounted in Track 2's gateway. Sessions use its `auth_sessions` collection and `wander_session` cookie. The demo-only sign-in is restricted to fixtures mode and loopback. The Astryx UI supports mobile, light/dark mode and keyboard navigation. Its explicit Next.js stream route prevents SSE proxy buffering.

`fixture_consumer.py` is a **demo substitute for Track 3**, guarded by fixture mode and a fixture board flag. Live requests stay queued for the real Track 3 consumer. Shared Pydantic/Mongo contracts are in `shared/models.py` and `shared/schemas/`; coordinate schema changes before merging.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# Real Atlas tests in a fresh isolated database:
$env:WANDER_RUN_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest -q
# Small functional provider requests and real delivery record checks:
.\.venv\Scripts\python.exe scripts/check_services.py
# From web/, with API and web servers running:
npm.cmd run typecheck
npm.cmd run build
npx.cmd playwright test
```

Atlas integration tests require permission to create `<MONGODB_DB>__shiv_test_<random>` and retain those synthetic databases as evidence. Browser tests use installed Chrome (override `CHROME_PATH` elsewhere), create a synthetic delay, confirm its scoped fix and save ignored screenshots under `web/test-results/`.

Coverage: idempotent setup/watchlists, offline recorded monitors, validators, eight simulation types within 30 seconds, signal dedupe, 30 watches claimed exactly once across two real processes, stale leases, transaction/ownership/double-submit checks, graph interrupt/resume, partial replanning, digest delivery, SSE latency, playable audio under 20 seconds, mobile, dark mode and keyboard interaction.

References: [MongoDBSaver](https://langchain-mongodb.readthedocs.io/en/latest/langgraph_checkpoint_mongodb/saver/langgraph.checkpoint.mongodb.saver.MongoDBSaver.html), [ElevenLabs agent API](https://elevenlabs.io/docs/api-reference/agents/get), [Resend sending API](https://resend.com/docs/api-reference/emails/send-email), [Astryx](https://github.com/meta/astryx).
