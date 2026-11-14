"""Track 3 acceptance checks that the demo board alone doesn't prove (MASTERPLAN §8.3).

    python scripts/test_track3_acs.py [r02 r04 r06 r07 r08 r10 r11] [--trip trip_t3_lisbon] [--r10-full]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import get_db

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def r02(_: argparse.Namespace) -> None:
    """Repeat query hits cache; a toy agent runs all six protocol steps on recorded search results."""
    from shared import agent_protocol, http, search_client
    from shared.agent_protocol import Hit, Query, ResearchAgent, city_ctx, link_place

    calls = {"n": 0}
    real_request = http.request

    def counting(*a, **kw):
        calls["n"] += 1
        return real_request(*a, **kw)

    http.request = counting
    try:
        q = f"lisbon tasca recommendations {datetime.now(UTC):%Y%m%d%H%M}"
        search_client.search(q, k=3)
        first = calls["n"]
        search_client.search(q, k=3)
        check("R-02 repeat query served from search_cache", first >= 1 and calls["n"] == first,
              f"provider calls: first={first}, after repeat={calls['n']}")
    finally:
        http.request = real_request

    recorded = json.loads((ROOT / "fixtures/track3/recorded_search.json").read_text())

    class ToyAgent(ResearchAgent):
        kind = "toy"
        steps: list[str] = []

        def plan(self):
            self.steps.append("plan")
            return [Query(r["query"]) for r in recorded]

        def search(self, queries):
            self.steps.append("search")
            by_q = {r["query"]: r["results"] for r in recorded}
            return [Hit(q, [search_client.SearchResult(**x) for x in by_q[q.q]]) for q in queries]

        def extract(self, hits):
            self.steps.append("extract")
            return [{"name": r.title.split(" — ")[0], "url": r.url} for h in hits for r in h.results]

        def link(self, items):
            self.steps.append("link")
            city = city_ctx("Lisbon")
            return [{**i, "place_id": link_place(city, i["name"], "sight", create=False, geocode=False)} for i in items]

        def score(self, items):
            self.steps.append("score")
            return sorted(items, key=lambda i: i["place_id"] is None)

        def persist(self, items):
            self.steps.append("persist")
            return [i["place_id"] for i in items if i["place_id"]]

    out = ToyAgent({"_id": "job_toy", "kind": "toy", "params": {"city": "Lisbon"}}).run()
    check("R-02 toy agent runs plan→search→extract→link→score→persist on recorded fixtures",
          ToyAgent.steps == ["plan", "search", "extract", "link", "score", "persist"] and len(out["result_refs"]) >= 2,
          f"steps={ToyAgent.steps}, linked={len(out['result_refs'])}")
    _ = agent_protocol


def r04(_: argparse.Namespace) -> None:
    """A place praised on Reddit + forums outranks one hyped by a single blog; 'Reddit + forums agree' chip."""
    from track3_research.consensus import build_chips, score_pipeline

    db = get_db()
    now = datetime.now(UTC)
    a, b = "pl_t3test_r04_a", "pl_t3test_r04_b"
    geo = {"type": "Point", "coordinates": [-9.14, 38.71]}
    base = {"city": "Lisbon", "category": "food", "source": "search", "geo": geo, "created_at": now, "updated_at": now}
    ops = [
        (a, "reddit", "https://www.reddit.com/r/lisbon/comments/t3test", 0.8, 1.0, False),
        (a, "forum", "https://www.tripadvisor.com/ShowTopic-t3test", 0.7, 0.9, False),
        (b, "blog", "https://example-travel-blog.com/t3test", 1.0, 0.7, True),
    ]
    try:
        db.places.insert_many([{**base, "_id": a, "source_id": a, "name": "T3 Test Tasca"}, {**base, "_id": b, "source_id": b, "name": "T3 Test Hype Spot"}])
        db.opinions.insert_many([{
            "_id": f"op_t3test_r04_{i}", "city": "Lisbon", "place_id": pid, "place_name": pid, "source_type": st, "source_url": url,
            "source_name": st, "summary": "test opinion", "stance": "pro", "sentiment": s, "weight": w, "ad_like": ad,
            "tags": ["grilled fish"], "themes": ["food"], "fetched_at": now, "posted_at": now, "expires_at": now.replace(year=now.year + 1)}
            for i, (pid, st, url, s, w, ad) in enumerate(ops)])
        flt = {"place_id": {"$in": [a, b]}}
        list(db.opinions.aggregate(score_pipeline("Lisbon", flt)))
        build_chips("Lisbon", flt)
        pa, pb = db.places.find_one({"_id": a}), db.places.find_one({"_id": b})
        sa, sb = pa["consensus"]["score"], pb["consensus"]["score"]
        check("R-04 Reddit+forums place outranks single-blog hype", sa > sb, f"{sa} vs {sb}")
        chips = [c["text"] for c in pa["consensus"].get("chips", [])]
        check("R-04 chip text 'Reddit + forums agree' generated", any(c.startswith("Reddit + forums agree") for c in chips), str(chips))
    finally:
        db.opinions.delete_many({"_id": {"$regex": "^op_t3test_r04_"}})
        db.places.delete_many({"_id": {"$in": [a, b]}})


def r06(ns: argparse.Namespace) -> None:
    db = get_db()
    n = db.places.count_documents({"city": "Lisbon", "source": {"$ne": "fixture"}, "geo.type": "Point",
                                   "geo.coordinates.0": {"$gte": -180, "$lte": 180}, "geo.coordinates.1": {"$gte": -90, "$lte": 90}})
    check("R-06 Lisbon ≥40 places with valid GeoJSON", n >= 40, f"{n} places")
    stays = list(db.links.find({"trip_id": ns.trip, "kind": "stay", "validation.ok": True}, {"url": 1}))
    check("R-06 ≥3 working stay links", len(stays) >= 3, f"{len(stays)} validated stay links")


def r07(ns: argparse.Namespace) -> None:
    db = get_db()
    links = list(db.links.find({"trip_id": ns.trip}, {"kind": 1, "url": 1, "label": 1, "validation": 1, "booked": 1}))
    ok = [l for l in links if (l.get("validation") or {}).get("ok")]
    flight = [l for l in ok if l["kind"] == "flight" and any(d in l["url"] for d in ("google.com/travel/flights", "skyscanner", "flightaware"))]
    transfer = [l for l in ok if l["kind"] == "transfer"]
    sintra = [l for l in ok if l["kind"] == "train" and "sintra" in (l["url"] + (l.get("label") or "")).lower()]
    check("R-07 London→Lisbon flight link validated", bool(flight), ", ".join(l["url"][:60] for l in flight[:2]))
    check("R-07 airport→stay transfer link validated", bool(transfer), ", ".join((l.get("label") or l["url"])[:60] for l in transfer[:2]))
    check("R-07 Sintra train link validated", bool(sintra), ", ".join(l["url"][:60] for l in sintra[:2]))
    broken = [l for l in links if not (l.get("validation") or {}).get("ok")]
    print(f"     ({len(ok)} ok / {len(broken)} not ok links on trip)")


def r08(ns: argparse.Namespace) -> None:
    from track3_research.agents.tickets import RESELLERS

    db = get_db()
    tickets = list(db.links.find({"trip_id": ns.trip, "kind": "monument_ticket", "validation.ok": True}, {"url": 1, "label": 1, "provider": 1}))

    def official_for(*names: str) -> list[str]:
        return [t["url"] for t in tickets if any(n in (t.get("label") or "").lower() for n in names)
                and not any(r in urlparse(t["url"]).netloc for r in RESELLERS)]

    for label, names in (("Jerónimos", ("jerónimos", "jeronimos")), ("Belém Tower", ("belém", "belem")), ("Pena", ("pena",))):
        urls = official_for(*names)
        check(f"R-08 {label} resolves to an official page", bool(urls), urls[0] if urls else "none")
    events = list(db.links.find({"trip_id": ns.trip, "kind": "club_ticket", "validation.ok": True, "event.date": {"$exists": True}}, {"event": 1}))
    check("R-08 ≥5 dated events", len(events) >= 5, f"{len(events)}: " + ", ".join(f"{e['event'].get('name', '')[:28]} {e['event']['date']}" for e in events[:6]))
    rest = db.links.distinct("item_ref", {"trip_id": ns.trip, "kind": "restaurant", "validation.ok": True})
    check("R-08 ≥5 reservable restaurants", len(rest) >= 5, f"{len(rest)} restaurants with validated reservation links")


def r10(ns: argparse.Namespace) -> None:
    """Pre-marked jobs → planner proceeds immediately; half failed → gaps flagged (and, with --r10-full, still plans)."""
    from track3_research.planner import graph as G

    db = get_db()
    src = db.trips.find_one({"_id": ns.trip})
    tid = "trip_t3_r10_test"
    db.trips.replace_one({"_id": tid}, {**src, "_id": tid, "status": "ready_for_research", "board_id": None}, upsert=True)
    db.research_jobs.delete_many({"trip_id": tid})
    trip = G.load_trip(tid)
    jids = []
    specs = [("opinions", t.replace(" ", "_")) for t in G.OPINION_THEMES] + [("places", g) for g in G.PLACE_GROUPS] + [(k, "main") for k in G.FACT_KINDS]
    now = datetime.now(UTC)
    for i, (kind, suffix) in enumerate(specs):
        jid = f"job_{tid}_{kind}_{suffix}"
        jids.append(jid)
        status = "done" if i % 2 == 0 else "failed"
        db.research_jobs.insert_one({"_id": jid, "trip_id": tid, "kind": kind, "params": G.job_params(trip, theme=suffix), "status": status,
                                     "attempts": 3 if status == "failed" else 1, "claimed_by": "fixture", "claimed_at": now,
                                     "result_refs": [], "stats": {}, "error": "fixture failure" if status == "failed" else None, "created_at": now})
    t0 = time.time()
    st = G.dispatch_research({"trip_id": tid})
    st2 = G.await_research({"trip_id": tid, "job_ids": st["job_ids"], "inline_workers": False})
    took = time.time() - t0
    failed = sum(1 for i in range(len(specs)) if i % 2)
    check("R-10 pre-marked jobs → await returns immediately", took < 15, f"{took:.1f}s for {len(jids)} jobs")
    check("R-10 half failed → gaps flagged", len(st2["gaps"]) == failed, f"{len(st2['gaps'])} gaps: {st2['gaps'][:3]}…")
    if ns.r10_full:
        out = G.run_planner(tid, inline_workers=False)
        b = db.boards.find_one({"trip_id": tid})
        check("R-10 still plans with gaps", bool(b and b.get("days")), f"board {b and b['_id']} gaps={len(b.get('gaps') or out.get('gaps') or []) if b else '-'}")
        if b:
            db.boards.delete_one({"_id": b["_id"]})
            db.board_versions.delete_many({"board_id": b["_id"]})
    db.research_jobs.delete_many({"trip_id": tid})
    db.trips.delete_one({"_id": tid})
    for c in ("planner_checkpoints", "planner_checkpoint_writes"):
        db[c].delete_many({"thread_id": tid})


def r11(_: argparse.Namespace) -> None:
    """'authentic fado not touristy' ranks local picks above the famous trap."""
    from shared.embed import embed_query
    from track3_research.planner.retrieval import fused_candidates, score_candidates, sourced

    q = "authentic fado not touristy"
    cands = [c for c in fused_candidates("Lisbon", q, embed_query(q), ["bar", "club", "food"], k=40) if sourced(c)]
    ranked = score_candidates(cands, q, {}, top_k=len(cands))
    traps = [i for i, p in enumerate(ranked) if (p.get("consensus") or {}).get("tourist_trap")]
    top = [p["name"] for p in ranked[:5]]
    print(f"     top5: {top}")
    print(f"     traps at ranks: {[(i + 1, ranked[i]['name']) for i in traps]}")
    check("R-11 a flagged tourist trap is among the candidates", bool(traps), f"{len(cands)} candidates")
    check("R-11 local picks rank above the trap", bool(traps) and min(traps) >= 3,
          f"best trap rank {min(traps) + 1 if traps else '-'}")


TESTS = {"r02": r02, "r04": r04, "r06": r06, "r07": r07, "r08": r08, "r10": r10, "r11": r11}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*", default=list(TESTS))
    ap.add_argument("--trip", default="trip_t3_lisbon")
    ap.add_argument("--r10-full", action="store_true")
    ns = ap.parse_args()
    for t in ns.tests:
        print(f"{t}:")
        TESTS[t](ns)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
