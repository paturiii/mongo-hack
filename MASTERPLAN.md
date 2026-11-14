# 🧭 Wander — End-to-end travel agent: asks the right questions, reads what travelers really think, hands you one board of booking links, and keeps the trip on track when things go wrong

### Architecture + build spec · 3 independent tracks · zero-blocking split
**Stack:** LangGraph · **MongoDB Atlas (required, the backbone)** · Voyage AI · Claude API · MCP · ElevenLabs (voice) · LangSmith · **Astryx** (Meta's design system, all UI)

---

## 0. TL;DR

You say *"5 days in Lisbon in March, I like food and nightlife, flying TP1351 from London."*
Wander:

1. **Asks 3–6 sharp clarifying questions** with tappable options, like Claude's "ask the user" cards or Cursor's plan mode (budget band, pace, neighborhood vibe, must-dos, dealbreakers). It **remembers you**, so the next trip asks fewer.
2. **Sends research agents in parallel.** Opinion Scouts read what real travelers say across **Reddit, travel forums, blogs, review aggregates and recent news**, and merge it into a consensus. Fact Finders get places, stays, transit, tickets, events and restaurants.
3. **Builds a day-by-day plan** clustered by geography (MongoDB `$geoNear`) and ranked by cross-source consensus and your preferences.
4. **Resolves a booking link for every item**, door to door: flight/train there, airport transfer, stay, local transit, monument tickets, club nights and restaurant reservations.
5. **Pushes everything to one Shared Trip Board**, a live page updated by MongoDB change streams. You click and book. Wander never touches your card.
6. **Watches the trip while you travel (disruption management).** It covers flight delays and cancellations, strikes, weather, closures, cancelled events and dead links. When something breaks it alerts you (board banner, email, ElevenLabs voice briefing) with **ready-to-click fixes**, and replans only the affected days.

It ships on **two surfaces from one backend and one account**:
- **Plugin:** a hosted remote MCP server (`https://mcp.wander.app/mcp`) for Claude, ChatGPT or Cursor.
- **Native app:** Wander's own Next.js app built on **Astryx**, with its own server-side Claude key and ElevenLabs voice.

Both surfaces end the same way: **"Your board is ready → wander.app/b/xyz"**.

---

## 1. 🔑 Prerequisites: credentials needed to run the app

### 1.1 MongoDB is mandatory
**Wander does not start without MongoDB Atlas credentials.** There is no in-memory, SQLite or mock fallback. Fixtures are loaded *into* Atlas too.

- Every process (gateway, intake graph, research workers, planner, disruption monitors) imports `shared/config.py`. **On boot it fails fast** if `MONGODB_URI` is missing or the cluster is unreachable (`client.admin.command("ping")` must succeed within 5 s), with the message `MONGODB_URI missing/unreachable — see MASTERPLAN §1`.
- **Atlas, not local `mongod`.** We need Atlas Search, Atlas Vector Search, `$rankFusion`, change streams and transactions (replica set). Use a cluster on **MongoDB 8.x**, and check that `$rankFusion` is available on the chosen tier at H0. M0/Flex limits search indexes, so use **M10+ or sponsor credits** if available.
- Each member creates their **own database user** on the shared cluster (`readWrite` on `wander`). Never share one password. Add your IP or `0.0.0.0/0` for the hackathon only.
- `scripts/check_env.py` verifies every required key from §1.2 plus Mongo connectivity, Atlas Search/vector index status and the `$jsonSchema` validators. **Run it first. CI and `make dev` run it too.**

### 1.2 All keys

| Env var | Required? | Used by | Where to get it / sponsor perk |
|---|---|---|---|
| `MONGODB_URI`, `MONGODB_DB=wander` | **🔴 Required, app won't boot** | All tracks | Atlas → Database → Connect → Drivers. One DB user per member. |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_SMART` (`LLM_BASE_URL` optional) | 🔴 Required | Intake graph, research extraction, planner, disruption triage, native chat | Provider-agnostic, two tiers. **FAST** (cheap, high-volume: extraction, classification, triage) defaults to `claude-haiku-5-5`. **SMART** (best reasoning: intake, planning, refine, chat) defaults to `claude-sonnet-5-5`. Any provider with structured output works. |
| `VOYAGE_API_KEY` | 🔴 Required | Embeddings + reranker (opinions, places, memory) | **200M free tokens per participant.** Sign up with email → Organization → API Keys (shown once, save it). **Add a payment method** to unlock Tier 1 rate limits; nothing is charged within the allowance. `pip install voyageai`. Voyage team is on-site for top-ups. |
| `SEARCH_PROVIDER` + `SEARCH_API_KEY` | 🔴 Required for live research (not for fixtures/seeded demo) | Opinion Scouts, ticket/event discovery, strike/closure news | Tavily (recommended: `include_domains`, news topic) / Exa / Brave. Pick one at H0. |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=wander` | 🟡 Strongly recommended | Tracing for all graphs, evals | **$50 LangSmith credits + Deployments access.** Redeem via the LangChain form **within 10 days of the hackathon** (event name = this hackathon). A card is needed to display credits and is not charged. |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_VOICE_ID` | 🟡 Needed for voice features | Voice intake agent (Track 2) + voice disruption briefings (Track 1) | **1 month Creator plan free (131k credits).** Join the ElevenLabs Discord → `#coupon-codes` → "Start Redemption" → pick this event → use your Cerebral Valley registration email. **Only works if you checked in by 11 AM.** Fill the hacker feedback form after the event. |
| `PLACES_PROVIDER` + `PLACES_API_KEY` | 🟡 (OSM needs no key) | PlaceFinder, closure checks | Google Places API (New) / Foursquare / `osm` (Overpass, keyless fallback) |
| `TICKETMASTER_API_KEY` | 🟡 | NightlifeFinder, event-cancellation monitor | developer.ticketmaster.com (free) |
| `FLIGHT_STATUS_PROVIDER` + `FLIGHT_STATUS_API_KEY` | 🟡 Needed for live flight disruption | Track 1 flight monitor | AviationStack (free tier) or FlightAware AeroAPI. The simulator works without it. |
| Weather | none | Track 1 weather monitor | **Open-Meteo**, free, no key |
| `RESEND_API_KEY` | 🟢 Optional | Disruption + "board ready" emails, magic-link auth | resend.com (free tier) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 🟡 | Native sign-in | Google Cloud console (OAuth client) |
| `JWT_SECRET`, `OAUTH_ISSUER`, `TOKEN_ENCRYPTION_KEY` | 🔴 Required (generate locally) | Gateway auth, MCP OAuth, BYO-key encryption | `openssl rand -hex 32` |
| `PUBLIC_BASE_URL` + `NGROK_AUTHTOKEN` | 🔴 for MCP plugin demo | MCP connector + board links | ngrok reserved domain |

**Modes:** `WANDER_MODE=fixtures` (Mongo + Anthropic + Voyage only, so each track can build alone), `seeded` (adds pre-seeded corpus, used for the stage demo) or `live` (all keys).

---

## 2. ⚠️ Reality check on data sources (read before building)

| Source | Status (Nov 2026) | What we do |
|---|---|---|
| **Reddit Data API** | Self-service closed Nov 2025. New public API requests stopped Oct 31, 2026. Logged-out `.json` returns 403. | **No Reddit API.** Use the search API with `site:reddit.com`, then summarize. |
| **General traveler opinion** | Spread across Reddit, TripAdvisor forums, Rick Steves forum, travel blogs, Google review summaries, YouTube titles/descriptions and local news | **Opinion Scouts** (§5.2) query each source class through the search API with domain allow-lists, extract opinions with Claude, and merge into **cross-source consensus**. Never store full text: summaries, quotes of 20 words or fewer, and source URLs. |
| Amadeus Self-Service | Shut down Jul 2026 | Not used. |
| Places / POIs | Google Places (New) / Foursquare / OSM Overpass | Pick one at H0. OSM is the keyless fallback. |
| Events / clubs | Ticketmaster Discovery (free key) + search discovery of Resident Advisor / DICE | Discover, then **link out**. |
| Getting there + transit | Search discovery + deep-link builders (Google Flights, Skyscanner, Omio, FlixBus, Rome2Rio, national rail) | We **link**, not book. |
| Monuments | Official site via search first, else Tiqets / GetYourGuide | `is_official` badge. |
| **Flight status** | AviationStack / FlightAware AeroAPI | Poll watched flights. The simulator covers the demo. |
| **Weather** | Open-Meteo (no key) | Daily forecast per trip day and area. |
| **Strikes / closures / protests** | Search API news topic (last 72h) + Claude classifier | Area- and date-matched to trip items. |

**Product principle:** Wander **plans, links and watches**. It never books or pays, which keeps it hackathon-safe and legally simpler.

---

## 3. System architecture

```
 ┌──── TRACK 2 · BASELINE APP + MEMORY ──────────────────────────────────────────┐
 │  Claude/ChatGPT/Cursor ── remote MCP ─┐      Native app (Next.js + Astryx)     │
 │                                       ▼      chat · question cards · voice(11L)│
 │                 Gateway (FastAPI): /mcp /api /auth /b/{slug} (SSE)             │
 │  Intake graph: understand → gap_analysis → ASK ⏸ → trip.status=ready_for_research│
 │  Memory: MongoDBStore prefs + episodic trip memory (Voyage) → pre-fills slots  │
 └────────┬───────────────────────────────▲──────────────────────────▲────────────┘
          │ trips (ready_for_research)    │ boards (change stream)    │ alerts
          ▼                               │                           │
 ┌──── TRACK 3 · RESEARCH + PLANNING ─────┴────────┐   ┌──── TRACK 1 · DISRUPTION MGMT ─────┴──┐
 │ Planner graph: dispatch → await → retrieve     │   │ Watchlist from board → monitors:       │
 │  ($rankFusion + Voyage rerank) → cluster       │   │ flight · weather · strikes · closures · │
 │  ($geoNear) → schedule → compose board (txn)   │   │ events · link health · price            │
 │  → consume refine_requests → board vN          │◄──┤ → impact ($geoWithin) → options        │
 │ Workers: Opinion Scouts (reddit/forums/blogs/  │   │ → refine_requests + alerts (email,     │
 │  reviews/news) + Fact Finders + LinkResolver   │   │   board banner, ElevenLabs voice)      │
 └────────────────────────┬───────────────────────┘   └───────────────────┬────────────────────┘
                          ▼                                               ▼
                    ┌─────────────────────── MongoDB Atlas ─────────────────────────┐
                    │      the integration bus: collections + change streams        │
                    └────────────────────────────────────────────────────────────────┘
```

### Why the three tracks are independent
**Tracks never import each other's code. They only talk through MongoDB collections defined in §6.**

| Track | Writes | Reads | Hands off via |
|---|---|---|---|
| **1 Disruption** | `watches`, `signals`, `disruptions`, `alerts`, `refine_requests` (source=disruption), `price_snapshots`, `links.validation` | `trips`, `boards`, `links`, `places` | inserts `refine_requests`; Track 3 replans |
| **2 Baseline App + Memory** | `users`, `sessions`, `questions`, `trips` (intake fields), `refine_requests` (source=user), `memories`, `usage` | `boards`, `alerts`, `disruptions`, `research_jobs` (progress counts) | sets `trips.status = "ready_for_research"` |
| **3 Research + Planning** | `research_jobs`, `places`, `opinions`, `links`, `search_cache`, `boards`, `trips` (plan fields) | `trips`, `refine_requests`, `memories` (read-only prefs) | writes `boards` vN |

Each track has **fixtures** (§6.3), so it is demo-able alone from hour 0. Integration means pointing at the same cluster.

---

## 4. MongoDB: the backbone (where judges should look)

| MongoDB feature | Where it's used | Track |
|---|---|---|
| **Document model** | A board is one rich doc (days → slots → items → links), so rendering needs no joins | 3 |
| **Change streams** | Workers react to new `research_jobs`; planner reacts to `ready_for_research` trips and `refine_requests`; board and alerts update live in the UI; disruption engine reacts to new `signals` | 1, 2, 3 |
| **Atomic job queue** (`findOneAndUpdate`) | Research jobs and monitor polls are claimed exactly once, with retries | 3, 1 |
| **Atlas Vector Search + Voyage** | Opinions, places and **traveler memory** recall | 3, 2 |
| **Hybrid search (`$rankFusion`)** | "authentic fado not touristy" uses keyword and semantic search together | 3 |
| **Voyage reranker** | Top-50 → top-10 per category; disruption replacement candidates | 3, 1 |
| **Geospatial** (`2dsphere`, `$geoNear`, `$geoWithin`) | Day clustering; "is this strike or storm area touching my day 3?"; nearest open alternative | 3, 1 |
| **Aggregation pipelines** | Consensus score across sources; severity scoring; board stats | 3, 1 |
| **TTL indexes** | Search cache (7d), link validation (24h), signals (14d), sessions (30d) | 3, 1, 2 |
| **Time-series collections** | `price_snapshots`, `signals` (flight status, weather readings) | 1 |
| **Schema validation (`$jsonSchema`)** | Enforces §6 contracts so no track breaks another | 2 (owns setup) |
| **Transactions** | Board version + trip status + audit; disruption resolve + refine request | 3, 1 |
| **LangGraph `MongoDBSaver` + `MongoDBStore`** | Every graph is resumable (plugin → app handoff); long-term traveler memory | 1, 2, 3 |
| **Atlas Search autocomplete** | Destination autocomplete | 2 |
| **MongoDB MCP Server (official)** | Dev/admin data inspection in Claude during the build | 2 |

---

## 5. Common ground: how everything must be done (all tracks)

These rules apply to every track. If your code breaks one, it doesn't merge.

### 5.1 Engineering rules
1. **Mongo is the only integration point.** `brain`-style cross-imports are forbidden. `track1_disruption/`, `track2_app/` and `track3_research/` import only `shared/`.
2. **Schemas first.** Every collection has a `$jsonSchema` in `shared/schemas/`, plus Pydantic models generated in `shared/models.py`. Changing a schema needs a PR touching `shared/` and a ping to all three tracks.
3. **Every agent is a LangGraph graph** compiled with `MongoDBSaver` (`thread_id` = the entity id: `session_id`, `trip_id` or `disruption_id`). LangSmith tracing on, project `wander`, tag `track:1|2|3`.
4. **LLM calls use structured output** (Pydantic). No regex-parsing of free text. Models come only from env via `shared/config.py`: `LLM_MODEL_FAST` for high-volume extraction and classification, `LLM_MODEL_SMART` for reasoning, plus `VOYAGE_EMBED_MODEL` and `VOYAGE_RERANK_MODEL`. Never hardcode a model name.
5. **Every claim has a source.** Any opinion, tip, price or disruption shown to a user carries `source_url` + `source_type` + `fetched_at`. No source means it isn't shown.
6. **Quote budget:** at most 20 words verbatim per source. Store summaries, never full pages or comments.
7. **Every external call is cached** in `search_cache` (TTL) and goes through `shared/http.py` (timeouts, retries, polite user agent `WanderBot/0.1`).
8. **Link, never book.** No payments, no logins on third-party sites, no scraping behind auth.
9. **Statuses only move forward** along the flows in §6.2. Writers use conditional updates (`{status: <expected>}` in the filter).
10. **Fixtures parity:** every feature must run with `WANDER_MODE=fixtures`.

### 5.2 Research agent protocol (Track 3 builds it; Track 1 reuses the pattern for news and closure monitors)
Every research agent subclasses `shared/agent_protocol.py::ResearchAgent` and follows the **same six-step workflow**:

```
PLAN     → derive 3–6 queries from job.params (city, theme, dates, profile)
SEARCH   → search_client.search(q, include_domains=SOURCE_CLASS.domains, recency=…)  [cached]
EXTRACT  → LLM_MODEL_FAST structured output → Opinion | Fact items
LINK     → entity-link to places (fuzzy name + city + $geoNear ≤300 m); create stub if new
SCORE    → source weight × recency × consensus (see below)
PERSIST  → upsert with Voyage embedding; job.result_refs; status=done
```

**Opinion source classes** (one agent config each, same code):

| `source_type` | Domains / query shape | Weight |
|---|---|---|
| `reddit` | `site:reddit.com` r/{city}, r/travel, r/solotravel, r/{country} | 1.0 |
| `forum` | tripadvisor.com/ShowTopic, community.ricksteves.com, flyertalk (transit) | 0.9 |
| `blog` | non-commercial travel blogs (exclude affiliate-heavy and listicle farms by heuristic) | 0.7 |
| `review` | Places API rating/review summary, Google/TripAdvisor review snippets via search | 0.8 |
| `video` | YouTube titles/descriptions via search (no transcripts) | 0.5 |
| `news` | local news last 30 days (closures, renovations, new openings) | 0.9 for facts, 0.3 for taste |

**Consensus score** (aggregation pipeline, per place): `Σ(weight × sentiment × recency_decay) / Σ(weight)` where `recency_decay = 0.5^(age_years/2)`. It is boosted when **≥2 distinct source types agree** and penalized for single-source claims or ad-like text. The board shows chips like *"Reddit + forums agree: go before 10am"*.

### 5.3 Workflow conventions
- **Job lifecycle:** `queued → claimed → done | failed` (3 attempts with backoff). Stale claims (>2 min) are requeued.
- **Interrupts:** Human-in-the-loop points (questions, disruption option choice) use LangGraph `interrupt()`. They resume when the corresponding Mongo doc flips status. Graphs don't care which surface answered.
- **Timeouts:** Research waits at most 90 s, then plans with what it has and flags gaps. Disruption triage takes at most 30 s from signal to alert.
- **Idempotency:** Every writer upserts on a natural key (`thread_url`, `place source id`, `url`, `signal hash`).

### 5.4 Design system: Astryx, no vibe-coded UI
All UI (native app, board, alerts panel) uses **[Astryx](https://github.com/meta/astryx)**, Meta's open-source design system (React 19+, StyleX, 150+ accessible components, 7 themes, dark mode).

```bash
npm install @astryxdesign/core @astryxdesign/theme-neutral @stylexjs/stylex
npm install -D @astryxdesign/cli
# package.json → "astryx": "node node_modules/@astryxdesign/cli/clients/cli/bin/astryx.mjs"
npm run astryx -- component --list      # always check before building a component
```

Rules:
1. **Use an Astryx component or template before writing your own.** Run `npm run astryx -- component --list` first. Custom components are only allowed when nothing fits, and are built by composing Astryx primitives.
2. **One theme** (`theme-neutral` unless the team picks another at H0) via CSS custom-property overrides in `web/theme/wander.css`. **No raw hex colors, no ad-hoc spacing, no inline styles.** Use tokens only.
3. **No Tailwind gradient soup, no emoji-as-icons, no glassmorphism.** Use Astryx typography scale, spacing tokens and icon set.
4. `swizzle` (eject component source) only with a note in the PR explaining why.
5. Coding agents (Claude Code / Cursor) must read the Astryx `AGENTS.md` and use the CLI. Add the Astryx docs to the repo with `npx astryx docs global`.
6. Every screen ships with **light and dark mode, keyboard navigation and mobile width (375px)** checked.
7. Shared UI lives in `web/components/wander/` (QuestionCard, BookingButton, OpinionChip, DisruptionBanner, DayTimeline). Track 2 owns the app shell. Track 1 owns `web/app/trip/[id]/disruptions/` and `DisruptionBanner`.

---

## 6. Contracts (frozen at H0, the only shared surface)

### 6.1 Collections

```js
// users  (T2)
{ _id, email, name, plan: "free"|"pro", auth: { provider, sub },
  connected_clients: ["claude","chatgpt","cursor","native"],
  notify: { email: true, voice: false, phone_e164 }, created_at }

// sessions  (T2)
{ _id: "ses_…", user_id, surface: "mcp"|"native"|"voice", thread_id, trip_id,
  status: "asking"|"researching"|"planning"|"ready"|"refining"|"traveling",
  messages: [{ role, content, at }], created_at, expires_at /* TTL 30d */ }

// questions  (T2 writes + answers)
{ _id: "q_…", session_id, batch: 1,
  items: [{ id: "pace", prompt, type: "single"|"multi"|"free"|"date_range"|"number",
            options: [{ id, label }], why, required, prefilled_from_memory: false }],
  status: "open"|"answered", answers: {…}, answered_at }

// trips  (T2 writes intake fields; T3 writes plan fields)
{ _id: "trip_…", user_id, session_id,
  origin: { name, geo },                                  // end-to-end: where you start
  destination: { name, country, geo: { type:"Point", coordinates:[lng,lat] } },
  dates: { start, end, flexible: false }, travelers: 2,
  legs: [{ kind: "flight"|"train"|"bus", carrier, number, from, to, dep_at, arr_at }], // user-provided or planned
  profile: { budget_band, pace, interests: [], dealbreakers: [], vibe: [], dietary: [], mobility },
  status: "intake"|"ready_for_research"|"researching"|"planned"|"traveling"|"done",
  board_id }

// research_jobs  (T3) ← queue
{ _id: "job_…", trip_id, kind: "opinions"|"places"|"stay"|"transit"|"getting_there"|"tickets"|"nightlife"|"restaurants"|"link_resolve",
  params: { city, theme, source_types: ["reddit","forum","blog","review","news"], dates },
  status: "queued"|"claimed"|"done"|"failed", attempts, claimed_by, claimed_at, result_refs, error, created_at }

// places  (T3)
{ _id, source: "google"|"osm"|"foursquare"|"search", source_id, name,
  category: "sight"|"food"|"bar"|"club"|"museum"|"stay"|"activity"|"transit_hub"|"beach"|"indoor",
  indoor: true|false,                                     // T1 uses for weather swaps
  geo, neighborhood, address, price_level, hours, rating, business_status,
  consensus: { score, mentions, by_source: { reddit: 9, forum: 3, blog: 2 }, tags: [], opinion_ids: [] },
  embedding, updated_at }

// opinions  (T3) — one per source item (generalized, not Reddit-only)
{ _id, place_id|null, city, source_type: "reddit"|"forum"|"blog"|"review"|"video"|"news",
  source_name: "r/lisbon", source_url, source_title,
  summary, quote /* ≤20 words */, stance: "pro"|"con"|"tip", sentiment: -1..1, tags: [],
  posted_at, weight, embedding, fetched_at, expires_at /* TTL */ }

// links  (T3 creates; T1 re-validates)
{ _id, trip_id, item_ref, kind: "flight"|"train"|"bus"|"transfer"|"stay"|"monument_ticket"|"club_ticket"|"restaurant"|"tour"|"rebook",
  provider, url, label, is_official, price_hint: { amount, currency, as_of }, for_date, party_size,
  validation: { status, checked_at, ok }, created_at }

// boards  (T3 writes; T2 renders; T1 annotates via alerts only)
{ _id: "b_…", trip_id, user_id, version, share_slug, title, summary,
  checklist_progress: { booked, total },
  days: [{ date, theme, area_geo, weather_hint,
    slots: [{ slot_id, time, item: { place_id, name, why,
              opinion_chips: [{ text, source_type, url }], link_ids: [], est_cost } }] }],
  bookings: [{ link_id, kind, label, url, status: "todo"|"user_marked_booked", deadline_hint }],
  updated_at }

// refine_requests  (T2 + T1 insert; T3 consumes)
{ _id, trip_id, source: "user"|"disruption", disruption_id?, instruction,
  scope: { days: ["2026-03-14"], slot_ids: [] }, constraints: { avoid_area_geo?, indoor_only?, shift_start_to? },
  status: "queued"|"claimed"|"done"|"failed", result_board_version, created_at }

// memories  (T2) — long-term, also mirrored in LangGraph MongoDBStore
{ _id, user_id, kind: "pref"|"episode"|"feedback",
  key: "pace", value: "relaxed", text: "Loved Alfama fado, hated the early Sintra start",
  confidence, source: "answer"|"refine"|"rating"|"disruption_choice", embedding, updated_at }

// watches  (T1)
{ _id, trip_id, kind: "flight"|"train"|"weather"|"area"|"venue"|"event"|"link"|"price",
  target: { carrier, number, date } | { place_id } | { link_id } | { area_geo, date },
  next_check_at, interval_s, active }

// signals  (T1) — TIME-SERIES (timeField: ts, metaField: meta), TTL 14d
{ ts, meta: { watch_id, kind, provider }, payload: {…}, hash }

// disruptions  (T1)
{ _id: "dis_…", trip_id, kind: "flight_delay"|"flight_cancel"|"weather"|"strike"|"closure"|"event_cancel"|"dead_link"|"price_spike",
  severity: 1..5, detected_at, evidence: [{ source_url, source_type, summary }],
  affected: { days: [], slot_ids: [], link_ids: [] },
  options: [{ id, label, actions: [{ type: "link"|"replan"|"notice", url?, instruction? }] }],
  status: "open"|"notified"|"resolved"|"dismissed", chosen_option, resolved_at }

// alerts  (T1 writes; T2 renders)
{ _id, user_id, trip_id, disruption_id, channel: "board"|"email"|"voice",
  title, body, audio_url?, status: "pending"|"sent"|"seen", created_at }

// price_snapshots  (T1) — TIME-SERIES
{ ts, meta: { link_id, provider, kind }, amount, currency }
```

### 6.2 Status flows (every track codes against these only)

```
trip:       intake ─T2─► ready_for_research ─T3─► researching ─► planned ─(start date)─► traveling ─► done
session:    asking ─► researching ─► planning ─► ready ◄─► refining ─► traveling
job:        queued ─► claimed ─► done | failed (attempts<3 → queued)
refine:     queued ─► claimed ─► done | failed
disruption: open ─► notified ─► resolved | dismissed      (resolved w/ replan → inserts refine_request)
board:      v1 ─► v2 … (each refine = new version, atomic transaction)
```

### 6.3 Fixtures (each track builds alone against these)
`/fixtures/lisbon/`: `user.json`, `session.json`, `questions.json`, `trip.json` (with a TP1351 leg), `research_jobs.json`, `places.json` (40), `opinions.json` (80, mixed source types), `links.json` (30), `board.json`, `memories.json`, `refine_requests.json`, `watches.json`, `signals.json`, `disruptions.json`.
`scripts/load_fixtures.py --city lisbon [--only a,b]` loads them **into Atlas** (Mongo is still required). Track 2 writes the fixtures in the first 90 min. Until then, use the schemas directly.

### 6.4 MCP tool surface (Track 2 exposes; tools only read/write Mongo)
| Tool | Purpose | Returns |
|---|---|---|
| `start_trip(prompt)` | Create session + trip, run intake | `session_id`, first `questions` batch (memory-prefilled) |
| `answer_questions(session_id, answers)` | Submit answers | next batch or `status: researching` |
| `get_status(session_id)` | Progress | `{status, jobs_done/total, board_url?}` |
| `get_board(session_id)` | Plan as markdown + URL | markdown + `board_url` |
| `refine(session_id, instruction)` | Inserts `refine_requests` | new board version when done |
| `find_places(city, query)` | Hybrid search over consensus-backed places | top 10 with source chips |
| `get_disruptions(trip_id)` | Open disruptions + options | list |
| `resolve_disruption(disruption_id, option_id)` | Pick a fix | updated board URL |
| `remember(fact)` / `forget(key)` | Edit long-term memory | ack |

---

## 7. Workflows

### 7.1 Intake graph (Track 2)
```
[load_memory] ─► [understand] ─► [gap_analysis] ─(gaps?)─yes─► [ask] ⏸ ─► gap_analysis
                                       │no
                                       ▼
                     [finalize_intake] trip.status = ready_for_research ─► [write_memory]
```
- ~15 slots (origin, dates, flexibility, travelers, budget, pace, interests, dealbreakers, stay type, vibe, nightlife intensity, dietary, mobility, must-sees, transport preference, **flight/train numbers if already booked**).
- `gap_analysis` scores **impact × uncertainty**, and **memory lowers uncertainty**. It asks the top 3–6 only, never anything inferable. Each question has 2–4 options, "Other" and a one-line *why*.

### 7.2 Planner graph (Track 3)
```
watch trips(ready_for_research) ─► [dispatch_research] ─► [await_research] ⏸ (≤90 s)
 ─► [retrieve] $rankFusion + Voyage rerank ─► [cluster_days] $geoNear ─► [schedule]
 ─► [compose_board] (txn) ─► status=planned
watch refine_requests(queued) ─► [scope] ─► partial re-research? ─► partial replan ─► board vN
```

### 7.3 Disruption graph (Track 1)
```
board planned ─► [build_watchlist] ─► monitors poll (interval by proximity to travel date)
signal ─► [detect] (rule + MODEL_FAST classifier) ─► [impact] $geoWithin / exact match on legs
 ─► [severity] ─► [options] (rebook links, alt transit, indoor swap, nearest open alternative)
 ─► [notify] alerts (board banner · email · ElevenLabs voice briefing) ─► ⏸ user picks option
 ─► [resolve] txn: disruption.resolved + insert refine_request ─► [learn] memory feedback
```

---

## 8. Tickets: three independent tracks

Legend: 🔴 must-have for demo · 🟡 should-have · 🟢 stretch · **Est** in hours.
**No ticket depends on another track's ticket.** Cross-track work happens only in the integration tickets (§8.4).

---

### 🚨 8.1 Track 1: Disruption Management

**Goal:** Once a board exists, Wander watches every leg, venue and day. It detects problems, explains impact with sources, and offers one-click fixes. **Demo-able solo** on `trip.json` + `board.json` fixtures + the simulator.

#### D-01 🔴 Disruption worker framework · 1.5h
- `track1_disruption/run.py`: a scheduler that claims due `watches` atomically (`findOneAndUpdate({next_check_at ≤ now, active:true})`), polls and writes `signals` (time-series, deduped by `hash`)
- Poll interval tightens near travel: >7 days → 6h, 1–7 days → 1h, travel day → 5 min (flights)
- **AC:** 30 fixture watches processed exactly once across 2 processes; duplicate signal is not reinserted

#### D-02 🔴 Watchlist builder · 1.5h
- Change stream on `boards` (new version) → upsert `watches`: each `trips.legs[]` flight/train, each day's area (weather), each ticketed venue, each event, each link, each priced stay/transit
- Removes watches for items dropped in newer board versions
- **AC:** fixture board produces the expected watch set; re-running is idempotent

#### D-03 🔴 Monitors · 3h
- `flight`: AviationStack/AeroAPI → delay minutes, cancel, gate/terminal change
- `weather`: Open-Meteo daily forecast → rain probability, wind, heat index for outdoor days
- `area`: news search (last 72h) for "{city} strike|protest|closure|metro" → Claude classifier → affected area (geocoded) + dates
- `venue`: Places `business_status` + news ("{venue} closed renovation")
- `event`: Ticketmaster event status (cancelled/postponed)
- All follow the §5.2 protocol (cache, source, quote budget)
- **AC:** each monitor produces a valid `signal` from a recorded HTTP fixture with no network (`WANDER_MODE=fixtures`)

#### D-04 🔴 Impact + severity engine · 2.5h
- Exact match for legs; `$geoWithin` of `area_geo` vs board slots on matching dates; `indoor:false` + bad weather → affected
- Severity 1–5 from aggregation: time-to-impact × items affected × bookings at risk (timed tickets, club nights)
- Writes `disruptions` with evidence + `affected`
- **AC:** simulated metro strike on Mar 14 in Baixa affects only Mar 14 slots inside the polygon; severity ≥ 3 when a timed ticket is affected

#### D-05 🔴 Response options (playbooks) · 3h
- `flight_delay` (>2h): shift day-1 slots, airline status/rebooking link, Google Flights alternative, late check-in note for the stay
- `flight_cancel`: rebooking links + alternatives (train/bus via Omio/Rome2Rio deep links) + "notify stay" template
- `weather`: swap outdoor ↔ indoor days (reads `places` `indoor:true`, Voyage rerank by profile)
- `strike`: alternative transit (walk/taxi/bus) + reorder
- `closure` / `event_cancel`: nearest similar open place via `$geoNear` + consensus score
- `dead_link`: fallback provider search link
- Each option has concrete `actions`. `replan` actions carry an `instruction` + `constraints` for Track 3
- **AC:** each kind yields 2–3 options with ≥1 clickable link; replan options carry valid `refine_requests` payloads

#### D-06 🔴 Resolve + handoff · 1h
- `resolve(disruption_id, option_id)` → transaction: disruption `resolved` + insert `refine_requests{source:"disruption"}` + audit
- Fixtures mode: a stub consumer marks refine `done` so the track demos alone
- **AC:** resolving writes both docs atomically; double-resolve is rejected

#### D-07 🔴 Disruption simulator · 1h
- `scripts/simulate_disruption.py --trip trip_lisbon --type flight_delay|flight_cancel|weather|strike|closure|event_cancel [--minutes 180]` writes realistic `signals` and runs the full pipeline
- **AC:** each type goes from signal to alert in ≤30 s; stage demo uses this

#### D-08 🔴 Alerts: board banner + email + voice · 2.5h
- Change stream on `disruptions` (open) → `alerts`
- `board`: `DisruptionBanner` (Astryx `Banner/Alert` + option buttons) + `web/app/trip/[id]/disruptions/` page
- `email`: Resend with option links (deep links to resolve)
- `voice`: **ElevenLabs TTS** briefing (≤20 s): *"Heads up — your TP1351 is delayed 3 hours. I've moved Time Out Market to tomorrow; tap to confirm."* Audio stored, URL in `alerts.audio_url`, playable on the board
- **AC:** simulated delay → banner visible on the open board within 2 s, email sent, voice clip plays

#### D-09 🟡 Link health + price watch · 1.5h
- Re-validate every board link every 24h (TTL'd `validation`) → `dead_link` disruptions
- Price snapshots for stays/transit → `price_spike` (>20% vs 7-day `$setWindowFields` avg) or "price dropped" notice
- **AC:** a 404'd fixture link raises a disruption with a fallback link

#### D-10 🟡 Memory feedback · 0.5h
- On resolve, write `memories{kind:"feedback", source:"disruption_choice"}` ("prefers trains over buses when rebooking")
- **AC:** memory doc written; Track 2 reads it at next intake (verified in I-04)

#### D-11 🟢 Proactive pre-trip checks · 1h
- T-48h digest: weather outlook, known strikes, sell-out risk on un-booked timed tickets
- **AC:** digest email lists ≥3 checks from fixtures

---

### 🏠 8.2 Track 2: Baseline App + Memory

**Goal:** The product people touch: DB foundation, accounts, intake conversation, question cards, live board, MCP plugin, voice, and long-term memory that makes Wander feel like it knows you. **Demo-able solo** on `boards`/`research_jobs`/`alerts` fixtures.

#### APP-01 🔴 Atlas setup + env gate + schemas + indexes · 2h
- `shared/config.py` fail-fast on missing/unreachable `MONGODB_URI` (§1.1); `scripts/check_env.py`
- DB `wander`, all collections from §6.1 with `$jsonSchema` validators; time-series `signals` + `price_snapshots`
- Indexes: `2dsphere` (`places.geo`, `days.area_geo` where needed), TTLs, unique natural keys, Atlas Search + Vector index JSON in `/infra/indexes/` (`opinions`, `places`, `memories`)
- **AC:** `scripts/setup_db.py` is idempotent; invalid docs rejected; app process exits with a clear error when `MONGODB_URI` is unset

#### APP-02 🔴 Fixtures pack · 1.5h (deliver by H1.5)
- All files in §6.3, schema-valid, real Lisbon places, mixed-source opinions clearly marked as fixtures
- **AC:** Tracks 1 and 3 can run their full flows on fixtures alone

#### APP-03 🔴 Gateway + auth · 2h
- FastAPI: `/api/*`, `/mcp` mount, `/b/{slug}` SSE, `/auth/*`
- Google OAuth + email magic link (Resend); **OAuth 2.1 endpoints for MCP** ("Add custom connector" sign-in)
- Fallback: API-key header auth for the demo connector
- **AC:** same `users` record whether signed in via app or via Claude connector

#### APP-04 🔴 Intake graph: understand + question engine · 3.5h
- Graph §7.1 with `MongoDBSaver`; `understand` → structured partial trip (origin, destination geocode, dates, legs, inferred interests; ambiguous = unknown, never guessed)
- `gap_analysis` impact × uncertainty (rules + LLM tiebreak); `ask` writes `questions` + `interrupt()`; resumes on `answered`
- Max 2 batches × 6 questions; sets `trips.status = ready_for_research`
- **AC:** "Lisbon in March, food + nightlife, flying TP1351" → asks budget, pace, stay vibe, nightlife intensity; does NOT ask interests or how you're getting there; leg saved

#### APP-05 🔴 Native app shell on Astryx · 3h
- Next.js (React 19) + Astryx `theme-neutral` + Wander token overrides; app shell, nav, auth screens
- Chat stream (server-side Claude key for conversational layer); `QuestionCard` (single/multi/date range/number/Other) from Astryx form components
- Progress view from `research_jobs` counts: "Reading r/lisbon, TripAdvisor forums… 41 opinions · Finding tickets… 3/5"
- Follows §5.4 (CLI-checked components, tokens only, dark mode, 375px)
- **AC:** full intake flow on fixtures; design review checklist (§5.4) passes

#### APP-06 🔴 Shared Trip Board (live) · 3h
- `/b/{slug}`: public-by-link, mobile-first, Astryx layout
- Day tabs → `DayTimeline` slots with `OpinionChip`s (source-type icon + link); map (MapLibre with `geo`)
- **Booking checklist** grouped Getting there · Stay · Getting around · Tickets · Nights out · Eat; "Mark booked" toggles; progress
- Live via change stream → SSE on `boards` + `alerts` (renders Track 1's `DisruptionBanner` slot)
- **AC:** editing a fixture board or inserting a fixture alert in Compass updates the open page within 2 s

#### APP-07 🔴 Remote MCP server (the plugin) · 3h
- Streamable HTTP MCP at `/mcp`, tools §6.4 (official MCP Python SDK); elicitation for questions where supported
- Tools only read/write Mongo; every terminal response includes `board_url`
- **AC:** add `https://<ngrok>/mcp` as a custom connector in Claude → `start_trip` → answer → board URL (fixtures)

#### APP-08 🔴 Long-term memory · 2.5h
- `MongoDBStore` namespaces `(user_id,"prefs")`, `(user_id,"episodes")` mirrored in `memories` with Voyage embeddings + vector index
- Write: answers → prefs; refines + disruption choices → feedback; trip end → episode summary
- Read: `load_memory` pre-fills slots (lowers uncertainty) and vector-recalls relevant episodes ("last time you found Sintra rushed")
- User control: "What Wander remembers" page (Astryx list) with edit/forget; MCP `remember`/`forget`
- **AC:** second trip for same user skips pace/budget and says "using your usual: relaxed, mid-range"; forgetting a pref makes it asked again

#### APP-09 🟡 Voice intake (ElevenLabs Agent) · 2h
- ElevenLabs Agent widget in native app; server tools `get_open_questions` / `submit_answers` read and write the same `questions` doc
- Voice ID shared with Track 1 briefings (`ELEVENLABS_VOICE_ID`) so Wander sounds consistent
- **AC:** complete question phase by voice; answers land in `questions.answers`

#### APP-10 🟡 Plugin ↔ app handoff · 1h
- "Continue in Wander" link from MCP results; same session, same board slug
- **AC:** start in Claude, refine in native app, same board

#### APP-11 🟡 Destination autocomplete · 1h
- Atlas Search autocomplete over cities + places
- **AC:** "lis" → Lisbon, Lisburn ranked sensibly

#### APP-12 🟢 BYO key + usage metering · 1.5h
- Optional own Anthropic key (encrypted with `TOKEN_ENCRYPTION_KEY`); otherwise per-user quota in `usage`
- **AC:** quota exceeded → friendly upsell; BYO key bypasses quota

#### APP-13 🟢 Board export + MongoDB MCP dev setup · 1h
- `.ics` per timed item, copy as text, print view; official MongoDB MCP server (read-only) configured for the team
- **AC:** .ics imports into Google Calendar; "how many opinions per source type?" answered from Claude

---

### 🔎 8.3 Track 3: Research + Planning

**Goal:** Turn a `ready_for_research` trip into a sourced, geographically sane, fully linked board, door to door. Owns all research agents (general opinion + facts) and the planner/refine engine. **Demo-able solo** on `trip.json` (status `ready_for_research`) + `refine_requests.json`.

#### R-01 🔴 Job worker framework · 2h
- `track3_research/workers/run.py`: change stream on `research_jobs` + startup sweep; atomic claim; retries/backoff; stale-claim reaper; asyncio pool of 8
- **AC:** 50 fixture jobs processed exactly once across 2 workers

#### R-02 🔴 Search client + cache + agent protocol · 2h
- `search(query, include_domains, recency, k)` over Tavily/Exa/Brave (swappable); `search_cache` TTL 7d
- `shared/agent_protocol.py::ResearchAgent` implementing the §5.2 six-step workflow (PLAN/SEARCH/EXTRACT/LINK/SCORE/PERSIST)
- **AC:** repeat query hits cache; a toy agent runs all six steps on recorded fixtures

#### R-03 🔴 Opinion Scouts (general opinion, multi-source) · 3.5h
- One agent, configured per `source_type` from §5.2 (reddit, forum, blog, review, video, news)
- Extracts `opinions` (summary, ≤20-word quote, stance, sentiment, tags), entity-links to places, Voyage embeds
- **AC:** "Lisbon food" yields ≥25 opinions from ≥3 source types linked to ≥10 places

#### R-04 🔴 Consensus scorer · 1.5h
- Aggregation pipeline (§5.2 formula) → `places.consensus` (score, by_source, top tags); cross-source agreement boost; ad/listicle penalty
- **AC:** a place praised on Reddit + forums outranks one hyped by a single blog; chip text "Reddit + forums agree" generated

#### R-05 🔴 Demo corpus pre-seeder · 1.5h
- `scripts/seed_corpus.py --city lisbon|tokyo|mexico-city` runs Opinion Scouts for ~10 themes × all source types, long TTL
- **AC:** 3 cities seeded with ≥150 opinions each across ≥3 source types

#### R-06 🔴 PlaceFinder + StayFinder · 2.5h
- Places by category + bbox → normalized `places` (`geo`, hours, price level, `indoor` flag); Voyage embedding
- Stay: 3 neighborhoods matching `vibe` from consensus → Booking.com / Airbnb deep links with dates, guests, area
- **AC:** Lisbon ≥40 places with valid GeoJSON; 3 working stay links

#### R-07 🔴 Getting there + TransitFinder · 2h
- Origin → destination: Google Flights / Skyscanner / Omio deep links (or user's booked leg), airport → stay transfer guidance + link
- Local transit passes, day trips (Lisbon → Sintra train) with national rail links
- **AC:** London → Lisbon flight search link, airport → Alfama transfer, Sintra train link, all validated

#### R-08 🔴 Tickets + Nightlife + Restaurants · 3h
- Tickets: official site first, then Tiqets/GYG; `is_official`; "book ahead" from consensus tags
- Nightlife: Ticketmaster + RA/DICE discovery for exact dates, merged with opinion tips (door policy, timing)
- Restaurants: consensus food picks → OpenTable / TheFork / Resy / official links; "walk-in only" tag otherwise
- **AC:** Jerónimos/Belém/Pena resolve to official pages; ≥5 dated events; ≥5 reservable restaurants

#### R-09 🔴 LinkResolver + Validator · 1.5h
- HEAD/GET check (follow redirects), strip tracking params, dedupe; only `ok` links reach boards
- **AC:** broken links never appear on a board; report script prints ok/broken counts

#### R-10 🔴 Planner graph: dispatch + await · 2h
- Change stream on `trips` `ready_for_research` → job plan from profile: `opinions×5` (food, nightlife, hidden gems, things to skip, neighborhoods), `places×3`, `stay`, `getting_there`, `transit`, `tickets`, `nightlife`, `restaurants`
- `await_research` resumes when all jobs are `done|failed` or after 90 s; reads prefs from `memories` (read-only)
- **AC:** fixture jobs pre-marked done → proceeds; half failed → still plans, flags gaps

#### R-11 🔴 Hybrid retrieval + rerank · 2.5h
- `$rankFusion` (Atlas Search on `name`, `consensus.tags`, `summary` + vector on `embedding`) per category → Voyage rerank top-50 → top-10
- Boost consensus score + pref match; penalize dealbreakers and "tourist trap" consensus
- **AC:** "authentic fado not touristy" ranks local picks above the famous trap

#### R-12 🔴 Day clustering + scheduling · 3h
- Stay = anchor; `$geoNear` + greedy clustering → day areas (`area_geo` polygon stored for Track 1)
- Pace-based item counts, meals near activities, nightlife on chosen nights, opening hours, timed slots, arrival-day buffer from `trips.legs`
- **AC:** no day spans more than ~4 km radius; museums never on closed days; day 1 starts after arrival + transfer

#### R-13 🔴 Board composition · 2h
- Attach `link_ids`, top 1–2 `opinion_chips` (multi-source preferred), est. cost; `bookings[]` ordered by urgency with `deadline_hint`
- Write `boards` vN + update `trips` in a **transaction**
- **AC:** fixture data → valid board passing `$jsonSchema`, every item sourced

#### R-14 🔴 Refine consumer · 2h
- Change stream on `refine_requests` (both `user` and `disruption`) → classify scope → partial re-research if needed → partial replan honoring `constraints` (`avoid_area_geo`, `indoor_only`, `shift_start_to`) → board v+1
- **AC:** "make day 3 a beach day" changes only day 3; fixture disruption refine (indoor_only on Mar 14) swaps only outdoor slots that day

#### R-15 🟢 Budget estimator · 1h
- Aggregation over `price_hint`s + per-day heuristics → board total range with confidence
- **AC:** board shows e.g. "€1,100–1,450 for 2"

---

### 🔗 8.4 Integration tickets (all three, timeboxed)

| ID | When | What | AC |
|---|---|---|---|
| I-01 🔴 | H0 (30 min) | Freeze §6 contracts; pick search/places/flight providers; create Atlas cluster + per-member DB users; everyone redeems perks (ElevenLabs before 11 AM, Voyage key + payment method, LangSmith) | `check_env.py` green on all 3 laptops |
| I-02 🔴 | H8 | **App → Research:** T2 intake sets `ready_for_research`, T3 produces a live Lisbon board | One live board from a real conversation |
| I-03 🔴 | H11 | **Board → Disruption:** T1 builds watches from T3's board; simulated strike → T3 refine → board v2 renders live in T2 | Banner + updated day within 30 s |
| I-04 🔴 | H14 | **Plugin + memory end-to-end** in Claude against live backend; second trip uses memory, including a disruption-learned pref | Board URL from Claude; fewer questions on trip 2 |
| I-05 🔴 | H18 | Feature freeze; demo script ×2 from clean seed | Passes twice |

---

## 9. Timeline (24h; scale as needed)

| Hours | Track 1: Disruption | Track 2: App + Memory | Track 3: Research + Planning |
|---|---|---|---|
| 0–2 | D-01, D-02 | APP-01, APP-02 | R-01, R-02 |
| 2–6 | D-03, D-07 | APP-03, APP-04 | R-03, R-04, R-05 (start seeding) |
| 6–10 | D-04, D-05 | APP-05, APP-06 | R-06, R-07, R-09, R-10 |
| 10–14 | D-06, D-08 | APP-07, APP-08 | R-08, R-11, R-12 |
| 14–18 | D-09, D-10, D-11 | APP-09, APP-10, APP-11, design polish | R-13, R-14, reseed 3 cities |
| 18–24 | Bug bash · demo rehearsal · backup video · README · sponsor feedback forms | | |

---

## 10. Repo layout

```
wander/
├── track1_disruption/     # Track 1
│   ├── run.py  watchlist.py  monitors/{flight,weather,area,venue,event,link,price}.py
│   ├── impact.py  playbooks/  resolve.py  alerts/{board,email,voice}.py
├── track2_app/            # Track 2
│   ├── gateway/ (FastAPI: api, mcp, auth, board SSE)
│   ├── intake/ (graph.py, understand.py, question_engine/)
│   └── memory/ (store.py, recall.py)
├── track3_research/       # Track 3
│   ├── workers/run.py  agents/{opinions,places,stay,getting_there,transit,tickets,nightlife,restaurants}.py
│   ├── consensus.py  link_resolver.py
│   └── planner/ (graph.py, retrieval.py, clustering.py, schedule.py, board.py, refine.py)
├── web/                   # Next.js + Astryx (T2 owns shell; T1 owns trip/[id]/disruptions + DisruptionBanner)
│   ├── app/  components/wander/  theme/wander.css
├── shared/
│   ├── config.py           # env + Mongo fail-fast
│   ├── agent_protocol.py   # §5.2 ResearchAgent base
│   ├── http.py  search_client.py  embed.py
│   ├── schemas/*.json      # $jsonSchema = the contract
│   └── models.py
├── fixtures/lisbon/ …
├── infra/indexes/*.json
└── scripts/ check_env.py  setup_db.py  load_fixtures.py  seed_corpus.py  simulate_disruption.py
```
**Rule:** `track1_*`, `track2_*`, `track3_*` may import `shared/` only, never each other.

---

## 11. Environment

The full template is in **`.env.example`** at the repo root. Copy it to `.env` (gitignored) and run `scripts/check_env.py`.
Required: `MONGODB_URI`, `MONGODB_DB`, `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_SMART`, `VOYAGE_API_KEY`, `JWT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `OAUTH_ISSUER`, `WANDER_MODE`. Everything else unlocks live research, disruption, voice, auth or tracing (see §1.2).

---

## 12. Demo script (3 min)

1. **(0:00) Hook:** "Planning a trip is 40 tabs of research, and the trip still goes sideways. Wander reads what travelers actually say, hands you one board of links, and fixes the plan when things break."
2. **(0:15) Plugin in Claude:** *"Lisbon, March 12–17, two of us, food and nightlife, flying TP1351."* Wander asks 4 sharp questions. It skipped budget and pace: *"using your usual: mid-range, relaxed"* (memory).
3. **(0:45) Research:** "Reading r/lisbon, TripAdvisor forums, 6 blogs… 112 opinions · finding tickets…". Show `research_jobs` flipping live in Atlas.
4. **(1:10) Board:** 5 days clustered on a map; chips like *"Reddit + forums agree: go before 10am"*; checklist from flight → transfer → stay → Sintra train → Pena official tickets → club night → reservations. Clean Astryx UI, dark mode.
5. **(1:40) Disruption:** run `simulate_disruption.py --type flight_delay --minutes 180`. The board banner slides in, the phone plays the ElevenLabs briefing, and you tap "Move dinner to tomorrow". Only day 1 changes, live.
6. **(2:15) Under the hood:** `$rankFusion` + Voyage rerank, `$geoNear` clustering, `$geoWithin` impact, change-stream queues, time-series signals, LangSmith traces of 3 graphs.
7. **(2:45) Close:** "Plan, book, travel: one board, two surfaces, and it watches your back. You just click."

---

## 13. Risks & mitigations

| Risk | Mitigation | Track |
|---|---|---|
| Reddit/forum access | Search API only + pre-seeded corpus; attribute and link; no raw text storage | 3 |
| Search API limits / cost | Mongo TTL cache; seed ahead; cap queries per job | 3, 1 |
| Missing MongoDB creds on a laptop | `check_env.py` at H0 (I-01); fail-fast message points to §1 | 2 |
| `$rankFusion` unavailable on tier | Check at H0; fallback: vector + text in `$unionWith` + manual reciprocal rank fusion | 3 |
| Flight API quota / no live delays | Simulator (D-07) drives the demo | 1 |
| False-positive disruptions | Require evidence URL + severity threshold; user can dismiss; never auto-replan without a tap | 1 |
| Deep-link formats change | Validator (R-09, D-09) + fallback to provider search page | 3, 1 |
| MCP OAuth fiddly | Start APP-03/07 early; API-key header fallback | 2 |
| UI looks vibe-coded | Astryx-only rule (§5.4) + design checklist in PR template | 2, 1 |
| ElevenLabs coupon missed | Redeem before 11 AM check-in deadline at I-01 | All |
| LLM latency | Parallel jobs; 90 s research timeout; stream progress; seeded corpus | 3 |
| Legal/ToS | Link out, never scrape behind logins, never book or pay; source on every claim | All |

---

## 14. Definition of done

- [ ] `check_env.py` green; app refuses to boot without MongoDB creds
- [ ] All 🔴 tickets merged; I-01 → I-05 passed
- [ ] Works as a **Claude custom connector** and in the **native app**, same account, same board
- [ ] Clarifying questions are few, sharp, tappable, with *why*, and **memory visibly reduces them on trip 2**
- [ ] Every board item has a source (≥1 opinion source type or provider); every bookable item has a validated link, door to door
- [ ] Simulated disruption → alert (banner + email + voice) → one-tap fix → partial replan, live
- [ ] UI is 100% Astryx components/tokens, light + dark, mobile
- [ ] MongoDB features in §4 visible in the demo or README
- [ ] Backup video + README + sponsor feedback forms (ElevenLabs, LangChain, Voyage) done
