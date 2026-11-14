# Track 3 — Research + Planning

Everything Track 3 owns from MASTERPLAN §8.3 (R-01..R-15). All state lives in the shared `wander` DB on Atlas.

## Setup

```bash
uv venv .venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
python -m track3_research.setup_db --wait      # indexes, TTLs, Atlas Search + Vector indexes (keeps existing validators)
```

Keys come from `.env` (see `.env.example`): `MONGODB_URI`, `OPENROUTER_API_KEY`, `VOYAGE_API_KEY`, `TAVILY_API_KEY`.
Without a Voyage payment method the account is limited to 3 RPM / 10K TPM; the Mongo-backed limiter in
`shared/embed.py` respects that across processes, but seeding is slow. Raise `VOYAGE_RPM`/`VOYAGE_TPM` once billing is on.

## Run

```bash
python -m track3_research.workers.run --concurrency 8      # R-01 research workers (claim jobs atomically, change-stream wakeups)
python -m track3_research.planner.run                      # planner + refine service (trips → ready_for_research, refine_requests → queued)

python -m track3_research.planner.run --trip trip_t3_lisbon --inline-workers   # one-shot plan
python -m track3_research.planner.run --refine rr_t3_beach                      # one-shot refine
python scripts/demo_track3.py                              # reset + plan Lisbon fixture trip + both refines
python scripts/verify_track3.py --trip trip_t3_lisbon      # acceptance checks on the written board
python scripts/seed_corpus.py --city lisbon                # R-05 (also tokyo, mexico-city)
python -m track3_research.link_resolver --report           # R-09 link health
```

## Layout

| Path | What |
|---|---|
| `shared/` | config, http (+ Mongo `search_cache`), LLM (OpenRouter structured output), Voyage embed/rerank, search, geo, agent protocol |
| `workers/run.py` | job queue: `findOneAndUpdate` claim, stale reaping, backoff, exactly-once (`scripts/test_workers.py`) |
| `agents/` | opinions, places (OSM), stay, getting_there, transit, tickets, nightlife, restaurants, link_resolve |
| `consensus.py` | R-04 aggregation pipeline → `$merge` into `places.consensus` (+ "Reddit + blogs agree" chips) |
| `link_resolver.py` | R-09 URL normalization + HEAD/GET validation, bot-protection aware |
| `planner/graph.py` | LangGraph: load → dispatch → await (change stream) → consensus → facts → retrieve → cluster → schedule → compose; `MongoDBSaver` checkpoints |
| `planner/retrieval.py` | R-11 `$rankFusion` (vector + Atlas Search) → Voyage rerank → consensus score |
| `planner/clustering.py` | R-12 `$geoNear` day clustering, opening hours, meals near activities, nightlife nights |
| `planner/board.py` | R-13 board composition, bookings, single-transaction write with `board_versions` archive |
| `planner/refine.py` | R-14 refine graph: scope → optional partial re-research → replan only the affected days → board v+1 |
| `planner/budget.py` | R-15 estimate from price hints + stay band + transit |

## Schema notes for other tracks

- Neighborhood nodes from OSM go to a separate `neighborhoods` collection (the `places.category` enum has no neighborhood).
- Transit passes are `links.kind = "train"` with `extra.transit_pass = true`.
- Every board item carries `sources: [{url, source_type, source_name}]`; stay-area picks live in `links` (`item_ref = "area:<slug>"`).
- Refines change only the targeted days; previous versions are kept in `board_versions` (`_id = "<board>@v<n>"`).
