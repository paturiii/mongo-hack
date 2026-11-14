# Track 1 verification

Verified on 2026-11-14 on local branch `shiv` against Atlas and production Next.js, using labeled synthetic Lisbon data.

| Ticket | Verified behavior |
| --- | --- |
| D-01 | Thirty due watches claimed once across two real processes; concurrent signal writes deduplicate; expired leases recover. |
| D-02 | Watch generation is idempotent; dropped board items deactivate their watches. |
| D-03 | Every recorded monitor runs with HTTP forbidden; configured LLM structured output, search, Voyage embeddings/reranking, ElevenLabs and Resend also passed functional checks. |
| D-04 | A March 14 strike affects only that date's slots within the polygon, including the timed ticket; severity is at least 3. |
| D-05 | All disruption kinds provide 2–3 concrete options, clickable links and scoped replan constraints. |
| D-06 | Ownership and option checks, concurrent double-submit rejection, atomic resolution/refine/audit/memory writes, graph interrupt/resume and scoped fixture replanning pass. |
| D-07 | All eight simulator kinds reach notification records within 30 seconds; repeated identical signals do not multiply them. |
| D-08 | Production Chrome verifies live SSE within 2 seconds of the board alert, a playable demo briefing, keyboard navigation, dark mode and 375px layout. A real Resend alert was accepted; the live ElevenLabs MP3 decoded in Chrome and lasted 6.7 seconds. |
| D-09 | A recorded HTTP 404 updates link validation and produces a dead-link observation. Price spikes/drops use a currency-specific seven-day baseline and repeated observations do not add snapshots. |
| D-10 | Resolution writes one disruption-choice feedback document atomically for Track 2 to consume. |
| D-11 | The fixture pre-trip digest contains weather, transport and unbooked-ticket checks and completes recorded email delivery. |

Final checks: **12 Python/Atlas tests passed**, **1 production Chrome end-to-end test passed**, TypeScript and production build passed, code checks passed. The strict environment/provider gate passed. Authenticated LangSmith access and a persisted `track:1` graph trace were verified. No pending demo alerts or queued demo signals remained after recovery.

The fixture consumer is explicitly a substitute for Track 3. Real replanning, native intake and memory recall across a second trip remain owned by Tracks 2/3 and are not represented as implemented here. Live flight polling is intentionally disabled at the user's request; simulator flights require no flight provider key. Live event polling requires the optional Ticketmaster key. Full live external data accuracy is not established by fixture acceptance tests.

Real email verification establishes provider acceptance, not an inbox delivery receipt. Tests retain isolated synthetic Atlas databases under `<MONGODB_DB>__shiv_test_<random>`. Personal recipients and API credentials remain in ignored `.env`; no secrets or personal email addresses are included in project sources.
