"""Planner graph (MASTERPLAN §7.2), LangGraph + MongoDBSaver (thread_id = trip_id):

dispatch_research → await_research (≤ RESEARCH_TIMEOUT_S) → score_consensus → retrieve ($rankFusion + Voyage rerank)
→ cluster_days ($geoNear) → schedule → compose_board (transaction) → trip.status = planned
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph

from shared.agent_protocol import city_ctx
from shared.config import get_client, get_db, settings
from track3_research.consensus import run_consensus
from track3_research.planner import board as B
from track3_research.planner.budget import estimate
from track3_research.planner.clustering import PER_DAY, cluster_days
from track3_research.planner.retrieval import retrieve
from track3_research.planner.schedule import arrival_info, schedule_day

log = logging.getLogger("wander.planner")

OPINION_THEMES = ["food", "nightlife", "hidden gems", "things to skip", "neighborhoods"]
PLACE_GROUPS = ["sights", "food", "nightlife"]
FACT_KINDS = ["stay", "getting_there", "transit", "tickets", "nightlife", "restaurants"]
CORPUS_FRESH = timedelta(days=14)


class PlanState(TypedDict, total=False):
    trip_id: str
    inline_workers: bool
    job_ids: list[str]
    gaps: list[str]
    consensus: dict
    facts: dict
    pools: dict
    plans: list[dict]
    days: list[dict]
    result: dict
    timings: dict


# ---------- helpers ----------
def as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def load_trip(trip_id: str) -> dict:
    db = get_db()
    trip = db.trips.find_one({"_id": trip_id})
    if not trip:
        raise ValueError(f"trip {trip_id} not found")
    start, end = as_date(trip["dates"]["start"]), as_date(trip["dates"]["end"])
    trip["_dates"] = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    prof = dict(trip.get("profile") or {})
    for m in db.memories.find({"user_id": trip.get("user_id"), "kind": "pref"}):
        if m.get("key") and prof.get(m["key"]) in (None, "", []):
            prof[m["key"]] = m.get("value")
    trip["profile"] = prof
    return trip


def job_params(trip: dict, **extra) -> dict:
    d = trip["destination"]
    return {"city": d["name"], "country": d.get("country"), "dates": {"start": trip["_dates"][0], "end": trip["_dates"][-1]},
            "travelers": trip.get("travelers") or 2, "origin": trip.get("origin"), "legs": trip.get("legs") or [],
            "profile": trip["profile"], **extra}


def _ts(state: PlanState, key: str, t0: float) -> dict:
    return {**(state.get("timings") or {}), key: round(time.time() - t0, 1)}


# ---------- nodes ----------
def dispatch_research(state: PlanState) -> dict:
    t0 = time.time()
    db = get_db()
    trip = load_trip(state["trip_id"])
    city = city_ctx(trip["destination"]["name"], trip["destination"].get("country"))
    db.trips.update_one({"_id": trip["_id"], "status": "ready_for_research"}, {"$set": {"status": "researching", "research_started_at": datetime.now(UTC)}})
    now = datetime.now(UTC)
    specs: list[tuple[str, str, dict]] = []
    for th in OPINION_THEMES:
        specs.append(("opinions", th.replace(" ", "_"), job_params(trip, theme=th, source_types=["reddit", "forum", "blog", "review", "video", "news"])))
    groups = PLACE_GROUPS + (["nature"] if any(i in ("beach", "beaches", "nature", "outdoors") for i in trip["profile"].get("interests") or []) else [])
    for g in groups:
        specs.append(("places", g, job_params(trip, theme=g)))
    for k in FACT_KINDS:
        specs.append((k, "main", job_params(trip, must_sees=trip["profile"].get("must_sees") or [])))
    ids = []
    for i, (kind, suffix, params) in enumerate(specs):
        jid = f"job_{trip['_id']}_{kind}_{suffix}"
        ids.append(jid)
        status, stats = "queued", {}
        if kind == "opinions":
            n = db.opinions.count_documents({"city": city["key"], "themes": params["theme"], "fetched_at": {"$gt": now - CORPUS_FRESH},
                                             "summary": {"$not": {"$regex": "^\\[FIXTURE"}}})
            if n >= 15:
                status, stats = "done", {"corpus_hit": n}
        elif kind == "places":
            cats = {"sights": ["sight", "museum"], "food": ["food"], "nightlife": ["bar", "club"], "nature": ["beach", "activity"]}[suffix]
            n = db.places.count_documents({"city": city["key"], "source": "osm", "category": {"$in": cats}, "updated_at": {"$gt": now - CORPUS_FRESH}})
            if n >= 100:
                status, stats = "done", {"corpus_hit": n}
        db.research_jobs.update_one({"_id": jid}, {"$setOnInsert": {
            "trip_id": trip["_id"], "kind": kind, "params": params, "status": status, "attempts": 0, "claimed_by": "corpus" if status == "done" else None,
            "claimed_at": None, "result_refs": [], "stats": stats, "error": None, "created_at": now + timedelta(milliseconds=i)}}, upsert=True)
    log.info("dispatched %d jobs for %s (%d corpus hits)", len(ids), trip["_id"],
             db.research_jobs.count_documents({"_id": {"$in": ids}, "claimed_by": "corpus"}))
    return {"job_ids": ids, "timings": _ts(state, "dispatch", t0)}


def await_research(state: PlanState) -> dict:
    t0 = time.time()
    db = get_db()
    ids = state["job_ids"]
    if state.get("inline_workers"):
        from track3_research.workers.run import run as run_workers

        threading.Thread(target=lambda: asyncio.run(run_workers(f"planner-inline:{state['trip_id']}", 8, exit_when_idle=5, trip_id=state["trip_id"])),
                         daemon=True).start()
    deadline = time.time() + settings.research_timeout_s
    pending = len(ids)
    with db.research_jobs.watch([{"$match": {"documentKey._id": {"$in": ids}}}], max_await_time_ms=2000) as stream:
        while time.time() < deadline:
            pending = db.research_jobs.count_documents({"_id": {"$in": ids}, "status": {"$nin": ["done", "failed"]}})
            if pending == 0:
                break
            stream.try_next()
    gaps = [f"{j['kind']}" + (f":{j['params'].get('theme')}" if j["params"].get("theme") else "") + f" ({j['status']})"
            for j in db.research_jobs.find({"_id": {"$in": ids}, "status": {"$ne": "done"}}, {"kind": 1, "status": 1, "params.theme": 1})]
    log.info("research settled in %.0fs: pending=%d gaps=%s", time.time() - t0, pending, gaps)
    return {"gaps": gaps, "timings": _ts(state, "await_research", t0)}


def score_consensus(state: PlanState) -> dict:
    t0 = time.time()
    trip = load_trip(state["trip_id"])
    res = run_consensus(trip["destination"]["name"], geocode_limit=25)
    return {"consensus": res, "timings": _ts(state, "consensus", t0)}


def gather_facts(trip: dict) -> dict:
    db = get_db()
    gt = db.research_jobs.find_one({"trip_id": trip["_id"], "kind": "getting_there", "status": "done"}) or {}
    airports = (gt.get("stats") or {}).get("airports") or {}
    airport_id = (gt.get("stats") or {}).get("airport_place_id")
    airport = db.places.find_one({"_id": airport_id}, {"name": 1, "geo": 1}) if airport_id else None
    stays: dict[str, dict] = {}
    for l in db.links.find({"trip_id": trip["_id"], "kind": "stay", "validation.ok": True}):
        a = l.get("stay_area") or {}
        st = stays.setdefault(a.get("name", "?"), {**a, "link_ids": []})
        st["link_ids"].append(l["_id"])
    stay_list = sorted(stays.values(), key=lambda s: s.get("rank", 9))
    city = city_ctx(trip["destination"]["name"])
    stay = stay_list[0] if stay_list else {"name": "City center", "geo": city["geo"], "why": "central base", "sources": ["https://www.openstreetmap.org/"], "link_ids": [], "rank": 0}
    flights = list(db.links.find({"trip_id": trip["_id"], "kind": "flight", "validation.ok": True}))
    events = list(db.links.find({"trip_id": trip["_id"], "kind": "club_ticket", "validation.ok": True, "event": {"$exists": True}},
                                {"event": 1, "label": 1, "url": 1, "provider": 1, "for_date": 1, "price_hint": 1}))
    passes = [l["_id"] for l in db.links.find({"trip_id": trip["_id"], "transit_pass": True, "validation.ok": True}, {"_id": 1})]
    return {"airports": airports, "airport": airport, "airport_id": airport_id, "stay": stay, "stay_options": stay_list,
            "flight_link_ids": [f["_id"] for f in flights], "events": events, "pass_link_ids": passes}


def _slim(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("embedding",)}


def retrieve_node(state: PlanState) -> dict:
    t0 = time.time()
    trip = load_trip(state["trip_id"])
    prof = trip["profile"]
    n = len(trip["_dates"])
    per_day = PER_DAY.get(str(prof.get("pace") or "balanced").lower(), 3)
    pools = retrieve(trip["destination"]["name"], prof, {"sights": per_day * n + 4, "food": 2 * n + 4, "nightlife": n + 2})
    facts = gather_facts(trip)
    return {"pools": {k: [_slim(p) for p in v] for k, v in pools.items()}, "facts": facts, "timings": _ts(state, "retrieve", t0)}


def cluster_node(state: PlanState) -> dict:
    t0 = time.time()
    trip = load_trip(state["trip_id"])
    prof, facts = trip["profile"], state["facts"]
    per_day = PER_DAY.get(str(prof.get("pace") or "balanced").lower(), 3)
    a = facts.get("airports") or {}
    arrival = arrival_info(trip.get("legs") or [], a.get("dest_airport"), a.get("dest_timezone") or "UTC", int(a.get("transfer_minutes") or 45), per_day)
    plans = cluster_days([as_date(d) for d in trip["_dates"]], facts["stay"]["geo"], state["pools"], prof, facts["events"],
                         arrival_capacity=arrival["capacity"])
    for p in plans:
        p["date"] = p["date"].isoformat()
    return {"plans": plans, "facts": {**facts, "arrival": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in arrival.items()}},
            "timings": _ts(state, "cluster", t0)}


def schedule_node(state: PlanState) -> dict:
    t0 = time.time()
    trip = load_trip(state["trip_id"])
    per_day = PER_DAY.get(str(trip["profile"].get("pace") or "balanced").lower(), 3)
    arrival = dict(state["facts"]["arrival"])
    if arrival.get("arr_local"):
        arrival["arr_local"] = datetime.fromisoformat(arrival["arr_local"])
    days = [{"date": p["date"], "kind": p["kind"], "slots": schedule_day(i, p, arrival, per_day)} for i, p in enumerate(state["plans"])]
    return {"days": days, "timings": _ts(state, "schedule", t0)}


def compose_ctx(trip: dict, facts: dict) -> dict:
    city = trip["destination"]["name"]
    stay = facts["stay"]
    transfer_ids = B.transfer_links(trip["_id"], facts.get("airport"), stay, city, int(trip.get("travelers") or 2))
    db = get_db()
    return {"refs": B.links_by_ref(trip["_id"]), "band": trip["profile"].get("budget_band") or "mid", "city": city, "stay": stay,
            "arrival": facts.get("arrival") or {"leg": None}, "airport_id": facts.get("airport_id"),
            "flight_links": list(db.links.find({"_id": {"$in": facts.get("flight_link_ids", [])}})),
            "transfer_ids": transfer_ids, "transfer_urls": [l["url"] for l in db.links.find({"_id": {"$in": transfer_ids}})]}


def finalize_days(trip: dict, plans: list[dict], days: list[dict], ctx: dict, pools: dict) -> list[dict]:
    out = []
    for plan, d in zip(plans, days):
        hero = plan["activities"][0] if plan["kind"] == "day_trip" and plan["activities"] else None
        c = {**ctx, "date": d["date"], "day_trip": plan.get("day_trip"), "day_trip_hero": hero}
        out.append({"date": d["date"], "kind": d["kind"], "theme": "", "area_geo": plan.get("area_geo"), "weather_hint": None,
                    "radius_m": plan.get("radius_m"), "outdoor_share": plan.get("outdoor_share"), "slots": B.build_slots(d["slots"], c)})
    return out


def assemble_board(trip: dict, days: list[dict], facts: dict, gaps: list[str], previous: dict | None) -> dict:
    stay = facts["stay"]
    link_ids: list[str] = list(facts.get("flight_link_ids", []))
    for d in days:
        for s in d["slots"]:
            link_ids += s["item"].get("link_ids", [])
    link_ids += stay.get("link_ids", []) + facts.get("pass_link_ids", [])[:1]
    bookings = B.build_bookings(link_ids, as_date(trip["_dates"][0]), (previous or {}).get("bookings"))
    budget = estimate(trip, days, [b["link_id"] for b in bookings])
    return {"days": days, "bookings": bookings, "budget": budget,
            "checklist_progress": {"booked": sum(1 for b in bookings if b["status"] == "user_marked_booked"), "total": len(bookings)},
            "stay": {"area": stay["name"], "why": stay.get("why"), "geo": stay.get("geo"), "link_ids": stay.get("link_ids", []), "sources": stay.get("sources", []),
                     "alternatives": [s["name"] for s in facts.get("stay_options", [])[1:3]]},
            "gaps": gaps, "generated_by": "track3_research"}


def compose_node(state: PlanState) -> dict:
    t0 = time.time()
    trip = load_trip(state["trip_id"])
    facts = state["facts"]
    ctx = compose_ctx(trip, facts)
    days = finalize_days(trip, state["plans"], state["days"], ctx, state["pools"])
    previous = get_db().boards.find_one({"trip_id": trip["_id"]})
    board = assemble_board(trip, days, facts, state.get("gaps") or [], previous)
    B.polish(board["days"], trip, trip["destination"]["name"], board["budget"]["text"])
    dates = trip["_dates"]
    board["title"] = trip.get("_title") or f"{trip['destination']['name']} · {as_date(dates[0]):%b %d}–{as_date(dates[-1]):%d}"
    summary = trip.get("_summary") or f"{len(dates)} days clustered by neighborhood."
    board["summary"] = f"{summary} Est. {board['budget']['text']}."
    res = B.write_board(trip, board, plan_status=True, reason="initial plan" if not previous else "replan")
    log.info("board %s v%d written for %s (slug %s)", res["board_id"], res["version"], trip["_id"], res["share_slug"])
    return {"result": res, "timings": _ts(state, "compose", t0)}


@lru_cache(maxsize=1)
def checkpointer() -> MongoDBSaver:
    return MongoDBSaver(get_client(), db_name=settings.mongodb_db, checkpoint_collection_name="planner_checkpoints",
                        writes_collection_name="planner_checkpoint_writes")


@lru_cache(maxsize=1)
def graph():
    g = StateGraph(PlanState)
    g.add_node("dispatch_research", dispatch_research)
    g.add_node("await_research", await_research)
    g.add_node("score_consensus", score_consensus)
    g.add_node("retrieve", retrieve_node)
    g.add_node("cluster_days", cluster_node)
    g.add_node("schedule", schedule_node)
    g.add_node("compose_board", compose_node)
    g.add_edge(START, "dispatch_research")
    for a, b in [("dispatch_research", "await_research"), ("await_research", "score_consensus"), ("score_consensus", "retrieve"),
                 ("retrieve", "cluster_days"), ("cluster_days", "schedule"), ("schedule", "compose_board")]:
        g.add_edge(a, b)
    g.add_edge("compose_board", END)
    return g.compile(checkpointer=checkpointer())


def run_planner(trip_id: str, inline_workers: bool = False) -> dict[str, Any]:
    cfg = {"configurable": {"thread_id": trip_id}, "tags": ["track:3", "planner"], "run_name": "wander-planner", "recursion_limit": 25}
    g = graph()
    snap = g.get_state(cfg)
    if snap and snap.next:
        log.info("resuming planner for %s at %s", trip_id, snap.next)
        return g.invoke(None, cfg)
    return g.invoke({"trip_id": trip_id, "inline_workers": inline_workers, "timings": {}}, cfg)
