# 🧭 Wander — The trip agent that asks the right questions, reads Reddit, and hands you one board of booking links

### Architecture + build spec · 3 people · zero-blocking split
**Stack:** LangGraph · MongoDB Atlas (the backbone) · Voyage AI · Claude API · MCP · ElevenLabs (voice intake) · LangSmith

---

## 0. TL;DR

You say *"5 days in Lisbon in March, I like food and nightlife."*
Wander:

1. **Asks 3–6 sharp clarifying questions** with tappable options, like Claude's "ask the user" cards or Cursor's plan mode (budget band, pace, neighborhood vibe, must-dos, dealbreakers).
2. **Sends research agents in parallel.** Reddit Scouts read what real travelers say. Place, Transit, Tickets and Nightlife Finders get the hard facts.
3. **Builds a day-by-day plan** clustered by geography (MongoDB `$geoNear`), ranked by Reddit sentiment and your preferences.
4. **Resolves a booking link for every item:** stay, buses/trains, monument tickets, club nights, restaurant reservations.
5. **Pushes everything to one Shared Trip Board**, a live page updated by MongoDB change streams. You click and book. Wander never touches your card.

It ships **Higgsfield-style on two surfaces from one backend and one account:**
- **Plugin:** a hosted remote MCP server (`https://mcp.wander.app/mcp`) you add to Claude, ChatGPT or Cursor. The host's model does the talking, and Wander's tools do the research and write to the same Board.
- **Native app:** Wander's own web app with its **own AI API key** (server-side Claude key) for people who don't use an AI assistant. It also has voice intake via ElevenLabs.

Both surfaces end the same way: **"Your board is ready → wander.app/b/xyz"**.

---

## 1. ⚠️ Reality check on data sources (read before building)

| Source | Status (Nov 2026) | What we do |
|---|---|---|
| **Reddit Data API** | Self-service closed Nov 2025. **New public API requests stopped Oct 31, 2026**, and remaining public access winds down through Mar 2027. Logged-out `.json` returns 403. | **Don't build on the Reddit API.** Reddit Scouts use a **web search API restricted to `site:reddit.com`** (Exa / Tavily / Brave Search) to get thread URLs and snippets, then summarize. Cache everything in Mongo (TTL), and pre-seed a demo corpus for 3 cities so the demo never depends on live search. Attribute and link back to every thread. |
| Amadeus Self-Service | Shut down Jul 2026 | Not used. |
| Places / POIs | Google Places API (New), or Foursquare Places, or OpenStreetMap Overpass (free) | Pick one at H0. OSM is the fallback with no key. |
| Events / clubs | Ticketmaster Discovery API (free key), Eventbrite, plus search-API discovery of Resident Advisor / DICE pages | Discovery via API or search, then **link out**. |
| Transit | Search-API discovery + deep-link builders (Omio, FlixBus, Rome2Rio, Busbud, national rail) | We **link**, not book. |
| Monuments / attractions | Official site found via search (preferred), else Tiqets / GetYourGuide | Prefer official links, with an "official" badge. |

**Product principle:** Wander **plans and links** and never books or pays. That makes it hackathon-safe, legally simpler and fast to build.

---

## 2. System architecture

```
 ┌──────────────── SURFACES (Member C) ────────────────────────────────────────┐
 │                                                                              │
 │  Claude / ChatGPT / Cursor          Wander Native App (Next.js)              │
 │  ┌──────────────────────┐          ┌──────────────────────────────────────┐ │
 │  │ Remote MCP connector │          │ Chat + Question Cards + Voice (11L)  │ │
 │  │ OAuth → Wander acct  │          │ Uses Wander's own Claude API key     │ │
 │  └─────────┬────────────┘          └───────────────┬──────────────────────┘ │
 │            │ MCP tools                              │ REST/SSE               │
 │  ┌─────────▼───────────────────────────────────────▼──────────────────────┐ │
 │  │        Wander Gateway (FastAPI): /mcp  /api  /auth  /b/{board_id}      │ │
 │  └─────────┬──────────────────────────────────────────────────────────────┘ │
 │            │                      ▲  change streams                          │
 │  ┌─────────┴─────────┐   ┌────────┴───────────────┐                          │
 │  │ Shared Trip Board │◄──┤  boards collection     │  (live link page)        │
 │  └───────────────────┘   └────────────────────────┘                          │
 └──────────────┬──────────────────────────────────────────────────────────────┘
                │ writes: sessions.messages, answers
                ▼
 ┌──────────────── BRAIN (Member A) ───────────────────────────────────────────┐
 │  LangGraph Orchestrator  (MongoDBSaver checkpoints · MongoDBStore memory)   │
 │  understand → gap_analysis → ASK (interrupt) → dispatch_research →          │
 │  await_research → cluster_days ($geoNear) → rank → compose_board → refine   │
 └──────────────┬───────────────────────────────────────────────▲──────────────┘
                │ inserts research_jobs                         │ reads places/opinions/links
                ▼                                               │
 ┌──────────────── SCOUTS (Member B) ────────────────────────────┴─────────────┐
 │  Job workers (watch research_jobs via change stream, claim atomically)      │
 │  RedditScout · PlaceFinder · StayFinder · TransitFinder · TicketFinder ·    │
 │  NightlifeFinder · LinkResolver/Validator                                   │
 │  → write places, opinions, links, offers (with Voyage embeddings)           │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   MongoDB Atlas     │  ← the integration bus
                         └─────────────────────┘
```

### Why this split is non-interdependent
**The three members never call each other's code. They only talk through MongoDB collections.**

- **A (Brain)** writes `research_jobs` and reads `places` / `opinions` / `links`. It writes `boards`.
- **B (Scouts)** reads `research_jobs` and writes `places` / `opinions` / `links`.
- **C (Surfaces)** writes `sessions` (messages, answers), reads `questions` and `boards`, and renders.

Each member gets a **fixtures file** (§4.3) matching the collection schemas, so they can build and demo their part alone from hour 0. Integration means pointing at the same cluster.

---

## 3. MongoDB: the backbone (where judges should look)

| MongoDB feature | Where it's used | Owner |
|---|---|---|
| **Document model** | A trip is a single rich doc (days → slots → items → links); no joins needed to render a board | A |
| **Change streams** | (1) Scouts react to new `research_jobs`. (2) Brain reacts to completed jobs. (3) The Board page updates live as items land. | B, A, C |
| **Atomic job queue** (`findOneAndUpdate`) | Workers claim jobs without double-processing, with retries and backoff | B |
| **Atlas Vector Search + Voyage embeddings** | Semantic search over Reddit opinions ("hidden gem", "tourist trap") and places | B (write), A (query) |
| **Hybrid search (`$rankFusion` of vector + Atlas Search full-text)** | "best pastel de nata not touristy" → both keyword and semantic | A |
| **Voyage reranker** | Rerank top-50 hybrid hits to top-10 per category | A |
| **Geospatial (`2dsphere`, `$geoNear`, `$geoWithin`)** | Cluster places into walkable day-plans; "near my stay" queries | A |
| **Aggregation pipelines** | Score = sentiment × pref match × distance penalty in one pipeline; board stats | A |
| **TTL indexes** | Expire search-API caches (7d), link validation results (24h), sessions | B, C |
| **Time-series collection** | `price_snapshots` for stays and transit (price trend sparkline on board) | B |
| **Schema validation (`$jsonSchema`)** | Enforce the contracts in §4 so nobody breaks anyone else | C (owns DB setup) |
| **Transactions** | Finalizing a board version (board + trip status + audit) atomically | A |
| **LangGraph `MongoDBSaver` + `MongoDBStore`** | Resume the multi-step workflow anywhere (plugin → app handoff); long-term traveler prefs | A |
| **Atlas Search autocomplete** | Destination and place autocomplete in the native app | C |
| **MongoDB MCP Server (official)** | Dev/admin: ask Claude about your data during the build; optional read-only "trip analytics" tool | C |

---

## 4. Contracts (frozen at H0, the only shared surface)

### 4.1 Collections

```js
// users  (C)
{ _id, email, name, plan: "free"|"pro", auth: { provider, sub },
  connected_clients: ["claude","chatgpt","cursor","native"],
  created_at }

// sessions  (C writes messages/answers; A writes state pointers)
{ _id: "ses_…", user_id, surface: "mcp"|"native", thread_id,
  trip_id, status: "asking"|"researching"|"planning"|"ready"|"refining",
  messages: [{ role, content, at }],
  answers: { budget_band: "mid", pace: "relaxed", ... },
  created_at, expires_at /* TTL 30d */ }

// questions  (A writes, C renders, C writes answer back)
{ _id: "q_…", session_id, batch: 1,
  items: [{
    id: "pace", prompt: "What pace do you want?",
    type: "single"|"multi"|"free"|"date_range"|"number",
    options: [{ id: "chill", label: "Chill — 2-3 things a day" },
              { id: "packed", label: "Packed — see everything" }],
    why: "Changes how many items per day", required: true }],
  status: "open"|"answered", answers: {…}, answered_at }

// trips  (A)
{ _id: "trip_…", user_id, session_id,
  destination: { name, country, geo: { type:"Point", coordinates:[lng,lat] } },
  dates: { start, end, flexible: false }, travelers: 2,
  profile: { budget_band, pace, interests: [], dealbreakers: [], vibe: [] },
  status: "draft"|"researching"|"planned"|"final", board_id }

// research_jobs  (A inserts, B claims/completes) ← the queue
{ _id: "job_…", trip_id, kind: "reddit"|"places"|"stay"|"transit"|"tickets"|"nightlife"|"link_resolve",
  params: { city:"Lisbon", query:"best fado bars", interests:["food"], dates:{…} },
  status: "queued"|"claimed"|"done"|"failed", attempts: 0,
  claimed_by, claimed_at, result_refs: [ObjectId], error, created_at }

// places  (B)
{ _id, trip_id|null /* null = global cache */, source: "google"|"osm"|"foursquare"|"search",
  name, category: "sight"|"food"|"bar"|"club"|"museum"|"stay"|"activity"|"transit_hub",
  geo: { type:"Point", coordinates:[lng,lat] }, neighborhood, address,
  price_level: 1-4, hours: {...}, rating,
  reddit: { mentions: 14, sentiment: 0.71, tags: ["hidden gem","go early"], opinion_ids: [] },
  embedding: [/* voyage */], updated_at }

// opinions  (B) — one per Reddit thread/comment cluster
{ _id, place_id|null, city, subreddit: "r/lisbon", thread_url, thread_title,
  summary: "Locals say skip X, go to Y instead", stance: "pro"|"con"|"tip",
  sentiment: -1..1, tags: [], upvotes_hint, posted_at,
  embedding: [/* voyage */], fetched_at, expires_at /* TTL */ }

// links  (B) — every bookable thing resolves to one of these
{ _id, trip_id, item_ref /* place_id or synthetic id */,
  kind: "stay"|"bus"|"train"|"flight"|"monument_ticket"|"club_ticket"|"restaurant"|"tour",
  provider: "official"|"booking"|"airbnb"|"omio"|"flixbus"|"tiqets"|"gyg"|"ra"|"dice"|"ticketmaster"|"opentable"|"thefork"|"google_flights",
  url, label: "Book Jerónimos tickets (official)", is_official: true,
  price_hint: { amount, currency, as_of }, for_date, party_size,
  validation: { status: 200, checked_at, ok: true }, created_at }

// price_snapshots  (B) — TIME-SERIES (timeField: ts, metaField: meta)
{ ts, meta: { link_id, provider, kind }, amount, currency }

// boards  (A writes, C renders live)
{ _id: "b_…", trip_id, user_id, version: 3, share_slug: "lisbon-mar-k3x9",
  title: "Lisbon · Mar 12–17 · food & nightlife",
  summary, checklist_progress: { booked: 4, total: 11 },
  days: [{
    date, theme: "Alfama & fado", area_geo: {…},
    slots: [{ time: "09:30", item: { place_id, name, why: "r/lisbon: 'go before 10'",
              reddit_quotes: [{ text, url }], link_ids: [], est_cost } }] }],
  bookings: [ { link_id, kind, label, url, status: "todo"|"user_marked_booked", deadline_hint } ],
  updated_at }
```

### 4.2 Status flow (every member codes against this only)

```
session: asking ──answers──► researching ──all jobs done──► planning ──► ready ◄──► refining
job:     queued ──claim──► claimed ──► done | failed(attempts<3 → queued)
board:   v1 draft ─► v2 … (each refine = new version, atomic transaction)
```

### 4.3 Fixtures (each member builds alone against these)
`/fixtures/lisbon/` contains: `session.json`, `questions.json`, `trip.json`, `research_jobs.json`, `places.json` (40), `opinions.json` (60), `links.json` (25), `board.json`. `scripts/load_fixtures.py --city lisbon` loads them. **C writes these in the first 90 min from the schemas above.** Until then everyone uses the schemas directly.

### 4.4 MCP tool surface (C exposes; implementations call into A's graph / Mongo)
| Tool | Purpose | Returns |
|---|---|---|
| `start_trip(prompt)` | Create session + trip, run understanding | `session_id`, first `questions` batch |
| `answer_questions(session_id, answers)` | Submit answers to the clarifying cards | next batch or `status: researching` |
| `get_status(session_id)` | Poll progress | `{status, jobs_done/total, board_url?}` |
| `get_board(session_id)` | Full plan as compact markdown + `board_url` | markdown + URL |
| `refine(session_id, instruction)` | "Swap day 3 for a beach day", "cheaper stay" | new board version |
| `find_places(city, query)` | Ad-hoc hybrid search over Reddit-backed places | top 10 with Reddit tags |

In MCP hosts, clarifying questions go out through **MCP elicitation** where the client supports it, and otherwise as structured tool results that the host model asks conversationally. The native app renders them as tappable cards.

---

## 5. The multi-step workflow (Brain graph)

```
[understand] ─► [gap_analysis] ─► (gaps?) ─yes─► [ask] ⏸ interrupt ─► back to gap_analysis
                                     │no
                                     ▼
                         [dispatch_research]  (insert N research_jobs)
                                     ▼
                         [await_research] ⏸ resumed by change stream when all jobs done/failed
                                     ▼
                [retrieve] hybrid search ($rankFusion) + Voyage rerank per category
                                     ▼
                [cluster_days] $geoNear around stay + k-means-lite → day areas
                                     ▼
                [schedule] respect hours, pace, opening times, travel time
                                     ▼
                [compose_board] attach link_ids, reddit quotes, est cost → boards v1 (txn)
                                     ▼
                [refine loop] ⏸ user instruction → partial replan → board vN
```

**Question engine ("ask like Claude/Cursor"):**
- A **trip slot schema** has ~15 slots (dates, flexibility, travelers, budget band, pace, interests, dealbreakers, stay type, neighborhood vibe, nightlife intensity, dietary, mobility, must-sees, transport preference between cities, booking-ahead tolerance).
- `gap_analysis` scores each slot: **impact on plan × uncertainty**. It asks only the top 3–6 and never a question it can infer (e.g. "nightlife" in the prompt means don't ask about interests in nightlife).
- Every question has **2–4 concrete options plus a free-text "Other"** and a one-line *why*.
- A second batch happens only if research surfaces a real fork (e.g. "Sintra day trip eats a full day — include it?").

---

## 6. Team split

| | **Member A — Brain** | **Member B — Scouts** | **Member C — Surfaces** |
|---|---|---|---|
| Owns | LangGraph orchestrator, question engine, retrieval/ranking, day clustering, board composition, refine loop | All research agents, Reddit-via-search pipeline, embeddings, link resolution and validation, job queue workers | MCP plugin server + OAuth, native app (own API key), Shared Trip Board (live), voice intake, DB setup and fixtures |
| Reads | `sessions`, `questions.answers`, `places`, `opinions`, `links` | `research_jobs` | `questions`, `boards`, `sessions` |
| Writes | `questions`, `trips`, `research_jobs`, `boards` | `places`, `opinions`, `links`, `price_snapshots`, job status | `users`, `sessions`, `questions.answers` |
| Demo-able solo with | fixtures for places/opinions/links | fixtures for research_jobs | fixtures for questions/boards |
| Mongo showcase | `$rankFusion`, `$geoNear`, aggregation scoring, transactions, checkpointer/store | change-stream queue, vector index writes, TTL caches, time-series | change-stream live board, `$jsonSchema`, Atlas Search autocomplete, MongoDB MCP server |

---

## 7. Tickets

Legend: 🔴 must-have for demo · 🟡 should-have · 🟢 stretch · **Est** in hours.
**No ticket depends on another member's ticket.** Cross-member work happens only in the integration tickets (§7.4).

### 7.1 🧠 Member A — Brain

#### A-01 🔴 Graph skeleton + persistence · 1.5h
- LangGraph `StateGraph` with all nodes from §5 as stubs, compiled with `MongoDBSaver` + `MongoDBStore`
- `thread_id = session_id`; LangSmith tracing on
- **AC:** a session runs end-to-end on stubs; killing the process at `ask` and restarting resumes correctly

#### A-02 🔴 `understand` node · 1h
- Claude call with structured output → partial `trip` doc (destination geocode, dates, travelers, interests inferred from prompt)
- **AC:** 10 sample prompts (fixtures) → correct partial trips; ambiguous dates marked unknown, never guessed

#### A-03 🔴 Question engine (`gap_analysis` + `ask`) · 3h
- Slot schema + impact×uncertainty scoring (rules + LLM tiebreak)
- Generates a `questions` doc (2–4 options, "Other", `why`), then `interrupt()`
- Resumes when `questions.status == "answered"` (A only watches the doc and doesn't care which surface answered)
- Max 2 batches, max 6 questions per batch
- **AC:** "Lisbon in March, food + nightlife" → asks budget, pace, stay vibe, nightlife intensity; does NOT ask about interests

#### A-04 🔴 Research dispatch + await · 2h
- Builds a job plan from the profile: e.g. `reddit×4` (food, nightlife, hidden gems, things to skip), `places×3`, `stay×1`, `transit×1` (airport + day trips), `tickets×1` (top monuments), `nightlife×1` (events on trip dates)
- Inserts `research_jobs`; `await_research` resumes via change stream when every job is `done|failed` **or** after a 90s timeout (plan with what you have)
- **AC:** with fixtures pre-marked `done`, graph proceeds; with half `failed`, still produces a plan and flags gaps

#### A-05 🔴 Hybrid retrieval + rerank · 3h
- Atlas Search index (`name`, `reddit.tags`, `summary`) + vector index (`embedding`)
- `$rankFusion` pipeline per category, then Voyage `rerank` top-50 → top-10
- Boost by `reddit.sentiment`, `reddit.mentions`, pref match; penalize `dealbreakers` and "tourist trap" tags
- **AC:** query "authentic fado not touristy" on fixtures ranks locals' picks above the famous trap

#### A-06 🔴 Day clustering + scheduling · 3h
- Stay location = anchor. `$geoNear` + simple greedy clustering → N day-areas
- Per day: 2–3 (chill) / 4–6 (packed) items, meals near activities, nightlife on chosen nights only
- Respect opening hours and ticketed time slots; walking time estimate (haversine × 1.3)
- **AC:** no day spans more than ~4km radius; museums never scheduled on closed days (fixture hours)

#### A-07 🔴 Board composition · 2h
- Attach `link_ids` to items, top 1–2 Reddit quotes (≤20 words, with thread URL), est. cost
- `bookings[]` checklist ordered by urgency (club tickets and timed monuments first, with `deadline_hint` like "sells out — book now")
- Write `boards` vN + update `trips` in a **transaction**
- **AC:** fixture data produces a valid board passing `$jsonSchema`

#### A-08 🟡 Refine loop · 2h
- `refine(instruction)` → LLM classifies scope (single slot / day / stay / whole trip) → partial re-run → board v+1
- Can enqueue new research jobs if needed ("add a beach day" → `places` job)
- **AC:** "make day 3 a beach day" only changes day 3; version increments

#### A-09 🟡 Long-term traveler memory · 1.5h
- `MongoDBStore` namespaces `(user_id, "prefs")`, `(user_id, "trips")`
- Answers and refinements become prefs; next trip pre-fills slots and asks fewer questions
- **AC:** second trip for same user skips pace/budget questions and says "using your usual: relaxed, mid-range"

#### A-10 🟢 Budget estimator · 1.5h
- Aggregation pipeline summing `price_hint`s + per-day food/transport heuristics → board total with confidence
- **AC:** board shows total range, e.g. "€1,100–1,450 for 2"

---

### 🔎 7.2 Member B — Scouts

#### B-01 🔴 Job worker framework · 2h
- `workers/run.py`: change stream on `research_jobs` (insert + status=queued) plus a startup sweep for missed jobs
- Atomic claim: `findOneAndUpdate({_id, status:"queued"}, {$set:{status:"claimed", claimed_by, claimed_at}})`
- Retries: on error `attempts++`, back to `queued` with backoff; 3 strikes → `failed`
- Stale-claim reaper (claimed > 2 min → requeue)
- Concurrency: asyncio pool of 8
- **AC:** 50 fixture jobs processed exactly once across 2 worker processes

#### B-02 🔴 Search-API client + cache · 1.5h
- Thin wrapper over Exa / Tavily / Brave (pick one at H0, keep interface swappable)
- `search(query, site=None, k=10)` → results; cached in `search_cache` with **TTL 7d** keyed by normalized query
- **AC:** repeat query hits cache; cache doc expires (verify with a short TTL in test)

#### B-03 🔴 RedditScout agent · 3h
- Input: `{city, theme}` → generates 3–5 queries (`site:reddit.com r/{city} best {theme}`, `"{city}" "tourist trap"`, `"{city}" "hidden gem" {theme}`)
- Fetches result pages/snippets via search API (no Reddit API), then Claude extracts **opinions**: place mentioned, stance, tip, sentiment, tags
- Entity-links each opinion to a `place` (fuzzy name + city; creates a stub place if new)
- Updates `places.reddit` rollup (mentions, avg sentiment, top tags)
- Embeds `opinions.summary` with Voyage
- **Never stores full comment text**: summaries plus short quotes (≤20 words) plus the thread URL
- **AC:** "Lisbon food" job yields ≥15 opinions linked to ≥8 places with sentiment

#### B-04 🔴 Demo corpus pre-seeder · 1.5h
- `scripts/seed_reddit_corpus.py --city lisbon|tokyo|mexico-city` runs RedditScout ahead of time for ~10 themes per city
- Saved with long TTL so the stage demo is instant and offline-safe
- **AC:** 3 cities seeded with ≥100 opinions each

#### B-05 🔴 PlaceFinder + StayFinder · 2.5h
- PlaceFinder: Places API (or OSM Overpass) by category + bbox → normalized `places` with `geo`, hours, price level; Voyage embedding of `name + category + tags`
- StayFinder: picks 3 neighborhoods matching `vibe` (from Reddit opinions on neighborhoods) → stay `links` (Booking.com / Airbnb search deep links with dates, guests and neighborhood) + `price_hint` if available
- **AC:** Lisbon returns ≥40 places with valid GeoJSON; 3 stay links that open correct searches

#### B-06 🔴 TransitFinder · 2h
- Airport → stay (metro/bus/taxi guidance + link), city-to-city and day trips (e.g. Lisbon → Sintra train)
- Deep-link builders: Omio / FlixBus / Rome2Rio / national rail / Google Flights for inter-city legs
- **AC:** Sintra day trip gets a train link plus "buy at station/app" note; links validated

#### B-07 🔴 TicketFinder (monuments & attractions) · 2h
- For top sights: search official site ticket page first (`"{sight}" official tickets`), then Tiqets / GetYourGuide fallback
- `is_official` flag; capture timed-entry and "sells out" signals from Reddit opinions (`tags: ["book ahead"]`)
- **AC:** Jerónimos / Belém Tower / Pena Palace resolve to official ticket pages

#### B-08 🔴 NightlifeFinder (clubs & events) · 2h
- Ticketmaster Discovery API (by city + dates) + search-API discovery of Resident Advisor / DICE event pages for the exact trip dates
- Merge with Reddit club opinions (dress code, door policy, "arrive before 1am")
- **AC:** returns ≥5 dated events for the trip window with ticket links, plus tips attached

#### B-09 🔴 LinkResolver + Validator · 1.5h
- Every link goes through HEAD/GET check (follow redirects; 200 → ok), stored in `links.validation` (TTL'd re-check 24h)
- Strip tracking params; dedupe identical URLs
- **AC:** broken links never reach `done`; validator report script prints ok/broken counts

#### B-10 🟡 Price snapshots (time-series) · 1.5h
- When a provider returns price, write to `price_snapshots` time-series; nightly/on-demand re-check for stays and transit
- **AC:** board can query 7-day price trend per link via `$setWindowFields`

#### B-11 🟡 Restaurant finder · 1.5h
- Reddit food opinions → places → OpenTable / TheFork / Resy / official site links when reservable; "walk-in only" tag otherwise
- **AC:** ≥5 reservable restaurants with links on a food-heavy trip

#### B-12 🟢 Opinion quality scorer · 1h
- Down-weight old threads (>3 years), single-mention claims and ad-like text; up-weight consensus across threads
- **AC:** consensus places outrank one-off mentions

---

### 🖥️ 7.3 Member C — Surfaces

#### C-01 🔴 Atlas setup + schema validation + indexes · 1.5h
- Cluster, DB `wander`, all collections from §4.1 with `$jsonSchema` validators
- Indexes: `2dsphere` on `places.geo`, TTL on `opinions.expires_at` / `sessions.expires_at` / `search_cache`, time-series `price_snapshots`, Atlas Search + vector index definitions committed as JSON in `/infra/indexes/`
- **AC:** `scripts/setup_db.py` is idempotent; invalid docs are rejected

#### C-02 🔴 Fixtures pack · 1.5h (deliver by H1.5)
- All files in §4.3 for Lisbon, schema-valid, realistic (real places, plausible Reddit-style summaries clearly marked as fixtures)
- `load_fixtures.py --city lisbon --only places,opinions` (selective loading lets each member mock others)
- **AC:** A and B can each run their full flow on fixtures alone

#### C-03 🔴 Gateway + auth · 2h
- FastAPI app: `/api/*` for native app, `/mcp` mount, `/b/{slug}` board page, `/auth/*`
- Auth: email magic link or Google OAuth for native; **OAuth 2.1 authorization server endpoints for MCP** (so "Add custom connector" in Claude does sign-in like Higgsfield)
- **AC:** same user record whether signed in via app or via Claude connector

#### C-04 🔴 Remote MCP server (the plugin) · 3h
- Streamable HTTP MCP server at `/mcp` exposing tools in §4.4 (official MCP Python SDK)
- Tools only read/write Mongo (`sessions`, `questions.answers`) and return; they **don't import A's code**. A's graph picks up changes via its own watchers.
- Elicitation for questions where supported; otherwise return question cards as structured content
- Every terminal response includes `board_url`
- **AC:** add `https://<ngrok>/mcp` as a custom connector in Claude, run `start_trip` → answer → get a board URL (on fixtures)

#### C-05 🔴 Native app: chat + question cards · 3h
- Next.js app. Chat stream; question cards rendered from `questions` doc (single / multi / date range / number / Other)
- **Uses Wander's own Claude API key server-side** for the conversational layer (greeting, explanations, refine phrasing); planning itself is the shared backend
- Progress view: "Reading r/lisbon… 14 opinions · Finding tickets… 3/5" (from `research_jobs` counts)
- **AC:** full flow on fixtures without the MCP surface

#### C-06 🔴 Shared Trip Board (live) · 3h
- `/b/{slug}`: public-by-link, mobile-first
- Day tabs → slots with Reddit quote chips (linked to threads), map (Leaflet/MapLibre with `geo`)
- **Booking checklist:** every link as a big button grouped by kind (Stay · Getting around · Tickets · Nights out · Eat); "Mark booked" toggles; progress bar
- **Live updates via change stream → SSE** on the `boards` doc; new version animates in
- **AC:** editing a fixture board in Compass updates the open page within 2s

#### C-07 🔴 Plugin ↔ app handoff · 1h
- Same session visible in native app ("Continue in Wander" link from MCP result); board is the same URL from either surface
- **AC:** start in Claude, finish refining in native app, same board slug

#### C-08 🟡 Voice intake (ElevenLabs) · 2h
- ElevenLabs Agent widget in native app: speak your trip; the agent asks the **same questions** by reading the open `questions` doc through a server tool `get_open_questions` and submits via `submit_answers`
- **AC:** complete the question phase by voice; answers land in `questions.answers`

#### C-09 🟡 Destination autocomplete · 1h
- Atlas Search autocomplete over `places` (cities) + seeded city list
- **AC:** "lis" → Lisbon, Lisburn ranked sensibly

#### C-10 🟡 BYO key + usage metering · 1.5h
- Native app: optional "bring your own Anthropic key" (encrypted at rest); otherwise Wander's key with per-user quota tracked in `usage` collection
- MCP surface uses the host's model, so it costs Wander only tool compute; show this in the pitch
- **AC:** quota exceeded → friendly upsell; BYO key bypasses quota

#### C-11 🟢 Board export · 1h
- "Add to calendar" (.ics per timed item), "Copy as text", print-friendly view
- **AC:** .ics imports into Google Calendar with links in descriptions

#### C-12 🟢 MongoDB MCP server dev setup · 0.5h
- Configure official MongoDB MCP server (read-only) for the team in Claude/Cursor to inspect data during dev; mention in pitch
- **AC:** "how many opinions per city?" answered from Claude during demo prep

---

### 7.4 🔗 Integration tickets (all three, timeboxed)

| ID | When | What | AC |
|---|---|---|---|
| I-01 🔴 | H0 (30 min) | Freeze §4 contracts; pick Places provider + search API; create shared Atlas cluster; share `.env` | Schemas committed |
| I-02 🔴 | H8 | **Brain ↔ Scouts on real data:** A's jobs get processed by B's workers, A plans from real places/opinions | One live Lisbon plan |
| I-03 🔴 | H10 | **Surfaces ↔ Brain:** C's answers resume A's interrupt; A's board renders live on C's page | Native app end-to-end |
| I-04 🔴 | H14 | **Plugin end-to-end in Claude** against live backend | Board URL from Claude chat |
| I-05 🔴 | H18 | Feature freeze; demo script ×2 from clean seed | Passes twice |

---

## 8. Timeline (24h; scale as needed)

| Hours | A — Brain | B — Scouts | C — Surfaces |
|---|---|---|---|
| 0–2 | A-01, A-02 | B-01, B-02 | C-01, C-02 |
| 2–6 | A-03, A-04 | B-03, B-04 (start seeding) | C-03, C-04 |
| 6–10 | A-05, A-06 | B-05, B-07, B-09 | C-05, C-06 |
| 10–14 | A-07, A-08 | B-06, B-08 | C-07, C-08 |
| 14–18 | A-09, polish prompts | B-10, B-11, reseed 3 cities | C-09, C-10, board polish |
| 18–24 | Bug bash · demo rehearsal · backup video · README | | |

---

## 9. Repo layout

```
wander/
├── brain/            # Member A
│   ├── graph.py  nodes/  question_engine/  retrieval/  clustering/  board.py
├── scouts/           # Member B
│   ├── workers/run.py  agents/{reddit,places,stay,transit,tickets,nightlife,restaurants}.py
│   ├── search_client.py  link_resolver.py  embed.py
├── surfaces/         # Member C
│   ├── gateway/ (FastAPI: api, mcp, auth, board SSE)
│   └── web/ (Next.js: chat, question cards, board, voice)
├── shared/
│   ├── schemas/*.json      # $jsonSchema = the contract
│   └── models.py           # Pydantic generated from schemas
├── fixtures/lisbon/ …
├── infra/indexes/*.json
└── scripts/ setup_db.py  load_fixtures.py  seed_reddit_corpus.py
```
**Rule:** `brain/`, `scouts/`, `surfaces/` may import `shared/` only, never each other.

---

## 10. Environment

```
MONGODB_URI=  MONGODB_DB=wander
ANTHROPIC_API_KEY=            # Wander's own key (native app + brain)
VOYAGE_API_KEY=               # embeddings + rerank
SEARCH_PROVIDER=exa|tavily|brave   SEARCH_API_KEY=
PLACES_PROVIDER=google|foursquare|osm   PLACES_API_KEY=
TICKETMASTER_API_KEY=
ELEVENLABS_API_KEY=  ELEVENLABS_AGENT_ID=
LANGSMITH_API_KEY=  LANGSMITH_TRACING=true  LANGSMITH_PROJECT=wander
PUBLIC_BASE_URL=              # ngrok/reserved domain for MCP + board
OAUTH_ISSUER=  JWT_SECRET=
```

---

## 11. Demo script (3 min)

1. **(0:00) Hook:** "Every travel app makes you do 40 tabs of research. Reddit already knows the answers. Wander reads it for you and hands you one board of links."
2. **(0:15) Plugin in Claude:** type *"Lisbon, March 12–17, two of us, into food and nightlife."* Claude (via Wander MCP) asks 4 sharp questions: budget band, pace, stay vibe, how late you go out. Answer by tapping.
3. **(0:45)** Progress: "Reading r/lisbon (32 threads)… finding tickets… checking club nights on your dates." Show MongoDB Atlas `research_jobs` flipping to `done` live (change streams).
4. **(1:15)** Claude returns the summary plus **board link**. Open it: 5 days clustered by neighborhood on a map, each pick with a Reddit quote chip, and a booking checklist: stay, Sintra train, Pena Palace official tickets, a club night on the 14th, two restaurant reservations.
5. **(1:50) Refine from the native app:** open the same board in Wander's app (own API key) and say by voice *"swap day 3 for a beach day."* The board updates live and only day 3 changes.
6. **(2:20) Under the hood:** one slide covering the MongoDB `$rankFusion` + Voyage rerank query, `$geoNear` clustering, change-stream queue and live board, and the LangSmith trace of the graph.
7. **(2:45) Close:** "Same backend, two surfaces: a plugin for people who live in Claude, an app for everyone else. You just click book."

---

## 12. Risks & mitigations

| Risk | Mitigation | Owner |
|---|---|---|
| Reddit content access (API closed to new apps) | Search-API only + pre-seeded corpus for demo cities; attribute and link; no bulk storage of raw text | B |
| Search API rate limits / cost | Mongo TTL cache; seed ahead; cap queries per job | B |
| Deep-link formats change | Validator (B-09) + fallback to provider search page | B |
| MCP OAuth fiddly | Start C-03/C-04 early; fallback = API-key header auth for the demo connector | C |
| LLM latency in multi-step flow | Parallel jobs; 90s research timeout; stream progress; pre-seeded corpus | A, B |
| Board looks empty mid-demo | Fixture-backed "demo mode" toggle per city | C |
| Legal/ToS | Link out, never scrape behind logins, never book or pay; show source on every claim | All |

---

## 13. Definition of done

- [ ] All 🔴 tickets merged; I-01 → I-05 passed
- [ ] Works as a **Claude custom connector** and in the **native app**, same account, same board
- [ ] Clarifying questions feel like Claude/Cursor: few, sharp, tappable, with *why*
- [ ] Every board item has a source (Reddit thread or provider) and every bookable item has a validated link
- [ ] Board updates live during the demo
- [ ] MongoDB features in §3 are visible in the demo or README
- [ ] Backup video + README + sponsor feedback forms done
