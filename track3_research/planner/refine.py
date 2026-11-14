"""R-14 Refine consumer (LangGraph + MongoDBSaver, thread_id = refine id):

claim → scope (LLM_MODEL_SMART + request scope/constraints) → re_research (only if needed) → replan (only scoped days/slots,
honoring avoid_area_geo / indoor_only / shift_start_to) → compose (board v+1 in a transaction) → refine.status = done
"""
from __future__ import annotations

import copy
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from shared import geo
from shared.agent_protocol import city_ctx
from shared.config import get_db
from shared.llm import structured
from track3_research.planner import board as B
from track3_research.planner.clustering import (
    DAY_RADIUS_M,
    MEAL_RADIUS_M,
    PER_DAY,
    _near,
    is_duplicate,
)
from track3_research.planner.graph import (
    as_date,
    assemble_board,
    checkpointer,
    gather_facts,
    load_trip,
)
from track3_research.planner.retrieval import retrieve
from track3_research.planner.schedule import schedule_day

log = logging.getLogger("wander.refine")

CategoryKey = Literal["sights", "food", "nightlife", "beach", "indoor"]


class RefineScope(BaseModel):
    days: list[str] = Field(description="affected dates YYYY-MM-DD (resolve 'day 3' etc. against the board dates)")
    slot_ids: list[str] = Field(description="specific slot ids affected, or empty for whole days")
    action: Literal["retheme_day", "swap_slots", "shift_times", "remove_slots", "annotate"]
    category_key: CategoryKey | None = Field(description="what kind of places the new items should be")
    theme_query: str | None = Field(description="semantic search query for replacements, e.g. 'best beaches for a beach day near Lisbon'")
    needs_research: bool = Field(description="true if the board's current research likely lacks suitable places (e.g. beaches never researched)")
    notice: str | None = Field(description="short practical note to show on affected days (e.g. strike advice), or null")


class RefineState(TypedDict, total=False):
    refine_id: str
    trip_id: str
    scope: dict
    changed: dict
    result: dict
    timings: dict


def _board_outline(board: dict) -> str:
    lines = []
    for i, d in enumerate(board["days"]):
        lines.append(f"Day {i + 1} = {str(d['date'])[:10]} ({d.get('theme', '')})")
        for s in d["slots"]:
            it = s["item"]
            lines.append(f"  {s['slot_id']} {s['time']} {it['name']} [{it.get('kind', '')}/{it.get('category', '')}, indoor={it.get('indoor')}]")
    return "\n".join(lines)


def claim(state: RefineState) -> dict:
    db = get_db()
    rr = db.refine_requests.find_one_and_update({"_id": state["refine_id"], "status": {"$in": ["queued", "claimed"]}},
                                                {"$set": {"status": "claimed", "claimed_at": datetime.now(UTC), "claimed_by": "track3-refine"}})
    if not rr:
        raise RuntimeError(f"refine {state['refine_id']} not claimable")
    return {"trip_id": rr["trip_id"], "timings": {}}


def scope(state: RefineState) -> dict:
    t0 = time.time()
    db = get_db()
    rr = db.refine_requests.find_one({"_id": state["refine_id"]})
    board = db.boards.find_one({"trip_id": rr["trip_id"]})
    if not board:
        raise RuntimeError("no board to refine yet")
    s = structured(RefineScope, "You scope an itinerary change request. Change as little as possible: only the days/slots the request is about.",
                   f"Request ({rr['source']}): {rr['instruction']}\nGiven scope: {rr.get('scope')}\nConstraints: {rr.get('constraints')}\n\nBoard:\n{_board_outline(board)}",
                   tier="smart").model_dump()
    given = rr.get("scope") or {}
    cons = rr.get("constraints") or {}
    if given.get("days"):
        s["days"] = [str(d)[:10] for d in given["days"]]
    if given.get("slot_ids"):
        s["slot_ids"] = given["slot_ids"]
    if cons.get("indoor_only"):
        s.update(action="swap_slots", category_key="indoor")
    if cons.get("avoid_area_geo"):
        s["action"] = "swap_slots"
        s["category_key"] = s.get("category_key") or "sights"
    if cons.get("shift_start_to"):
        s["action"] = "shift_times"
    s["constraints"] = cons
    log.info("refine %s scope: %s", state["refine_id"], {k: v for k, v in s.items() if k != "constraints"})
    return {"scope": s, "timings": {**(state.get("timings") or {}), "scope": round(time.time() - t0, 1)}}


def re_research(state: RefineState) -> dict:
    """Partial re-research inline (e.g. beaches never fetched): runs the agent through the same job queue contract."""
    t0 = time.time()
    s = state["scope"]
    trip = load_trip(state["trip_id"])
    city = city_ctx(trip["destination"]["name"])
    db = get_db()
    need_beach = s.get("category_key") == "beach" and db.places.count_documents({"city": city["key"], "category": "beach", "geo": {"$exists": True}, "embedding": {"$exists": True}}) < 5
    if s.get("needs_research") or need_beach:
        from track3_research.agents import agent_for
        from track3_research.consensus import run_consensus
        from track3_research.workers.run import claim as claim_job
        from track3_research.workers.run import finish

        theme = {"beach": "nature", "nightlife": "nightlife", "food": "food"}.get(s.get("category_key") or "", "sights")
        jid = f"job_{trip['_id']}_places_refine_{theme}"
        db.research_jobs.update_one({"_id": jid}, {"$setOnInsert": {"trip_id": trip["_id"], "kind": "places", "params": {"city": city["name"], "theme": theme},
                                                                    "status": "queued", "attempts": 0, "created_at": datetime.now(UTC)}}, upsert=True)
        job = claim_job(db, "refine-inline", trip["_id"])
        if job:
            res = agent_for(job["kind"])(job).run()
            finish(db, job, "refine-inline", res, None)
        if s.get("theme_query"):
            from track3_research.agents.opinions import OpinionScout
            OpinionScout({"_id": "job_refine_inline", "trip_id": trip["_id"], "kind": "opinions",
                          "params": {"city": city["name"], "theme": s["theme_query"][:60], "source_types": ["reddit", "blog", "forum"]}}).run()
        run_consensus(city["name"], geocode_limit=15)
        if s.get("category_key") == "beach":
            from track3_research.agents.places import embed_places
            embed_places(db, {"city": city["key"], "category": "beach", "embedding": {"$exists": False}}, city["name"], limit=120)
    return {"timings": {**(state.get("timings") or {}), "re_research": round(time.time() - t0, 1)}}


def _within(geom: dict | None, poly: dict | None) -> bool:
    if not geom or not poly:
        return False
    return _point_in_polygon(geom["coordinates"], poly["coordinates"][0])


def _point_in_polygon(pt, ring) -> bool:
    x, y = pt
    inside = False
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def replan(state: RefineState) -> dict:
    t0 = time.time()
    db = get_db()
    s = state["scope"]
    trip = load_trip(state["trip_id"])
    prof = trip["profile"]
    board = db.boards.find_one({"trip_id": trip["_id"]})
    new_days = copy.deepcopy(board["days"])
    for d in new_days:
        d["date"] = str(d["date"])[:10]
    on_board = {sl["item"].get("place_id") for d in new_days for sl in d["slots"] if sl["item"].get("place_id")}
    facts = gather_facts(trip)
    ctx = {**_ctx(trip, facts)}
    cons = s.get("constraints") or {}
    avoid = cons.get("avoid_area_geo")
    per_day = PER_DAY.get(str(prof.get("pace") or "balanced").lower(), 3)
    changed: dict[str, list[str]] = {}
    target_days = [d for d in new_days if d["date"] in set(s["days"])] or []

    if s["action"] == "retheme_day":
        key = s.get("category_key") or "sights"
        pools = retrieve(trip["destination"]["name"], prof, {key: per_day + 4, "food": 4}, overrides={key: s.get("theme_query")} if s.get("theme_query") else None,
                         exclude_ids=on_board)
        cands = [p for p in pools.get(key, []) if not (avoid and _within(p.get("geo"), avoid))]
        for d in target_days:
            if not cands:
                break
            dd = as_date(d["date"])
            seed = cands[0]
            ids = [p["_id"] for p in cands if geo.open_on(p.get("hours"), dd, p["category"])]
            near = _near(seed["geo"], ids, DAY_RADIUS_M * (3 if key == "beach" else 1))
            by_id = {p["_id"]: p for p in cands}
            acts: list[dict] = []
            for n in near:
                if len(acts) < per_day and not is_duplicate(by_id[n["_id"]], acts):
                    acts.append(by_id[n["_id"]])
            acts = acts or [seed]
            cands = [c for c in cands if not is_duplicate(c, acts)]
            centroid = geo.centroid([a["geo"] for a in acts])
            food_ids = [p["_id"] for p in pools.get("food", []) if p["_id"] not in on_board]
            lunch = next((p for n in _near(centroid, food_ids, MEAL_RADIUS_M * 4) for p in pools["food"] if p["_id"] == n["_id"]), None)
            old_night = next((sl for sl in d["slots"] if sl["item"].get("kind") == "night"), None)
            old_dinner = next((sl for sl in d["slots"] if sl["item"].get("kind") == "dinner"), None)
            plan = {"kind": "city", "activities": acts, "lunch": lunch, "dinner": None, "night": None}
            i = new_days.index(d)
            sched = schedule_day(i, plan, None, per_day)
            slots = B.build_slots(sched, {**ctx, "date": d["date"], "day_trip": None, "day_trip_hero": None})
            for keep in (old_dinner, old_night):
                if keep:
                    slots.append(keep)
            slots.sort(key=lambda x: x["time"])
            for j, sl in enumerate(slots):
                sl["slot_id"] = f"d{i + 1}s{j + 1}"
            d["slots"] = slots
            d["area_geo"] = geo.area_polygon([a["geo"] for a in acts] + ([lunch["geo"]] if lunch else []))
            d["theme"] = ""
            changed[d["date"]] = [sl["slot_id"] for sl in slots]

    elif s["action"] == "swap_slots":
        key = s.get("category_key") or "indoor"
        pools = retrieve(trip["destination"]["name"], prof, {key: 20, "food": 10}, overrides={key: s.get("theme_query")} if s.get("theme_query") else None,
                         indoor=True if cons.get("indoor_only") else None, exclude_ids=on_board)
        for d in target_days:
            dd = as_date(d["date"])
            for sl in d["slots"]:
                it = sl["item"]
                if s.get("slot_ids") and sl["slot_id"] not in s["slot_ids"]:
                    continue
                if not it.get("place_id") or it.get("kind") not in ("activity", "lunch", "dinner", "night"):
                    continue
                bad = (cons.get("indoor_only") and not it.get("indoor")) or (avoid and _within(it.get("geo"), avoid)) or (not cons and bool(s.get("slot_ids")))
                if not bad:
                    continue
                if it.get("kind") in ("lunch", "dinner"):
                    pool = pools["food"]
                elif it.get("kind") == "activity":
                    pool = [p for p in pools.get(key, []) if p["category"] not in ("food", "bar", "club")]
                else:
                    pool = pools.get(key, [])
                pool = [p for p in pool if p["_id"] not in on_board and geo.open_on(p.get("hours"), dd, p["category"])
                        and not (avoid and _within(p.get("geo"), avoid)) and (not cons.get("indoor_only") or p.get("indoor"))]
                if not pool:
                    continue
                near = _near(it.get("geo") or facts["stay"]["geo"], [p["_id"] for p in pool], 4000) or [{"_id": pool[0]["_id"]}]
                repl = next(p for p in pool if p["_id"] == near[0]["_id"])
                on_board.add(repl["_id"])
                kind = it.get("kind") if it.get("kind") in ("lunch", "dinner", "night") else "activity"
                sl["item"] = B.place_item(repl, kind, ctx["refs"], ctx["band"], d["date"])
                sl["item"]["replaced"] = it["name"]
                changed.setdefault(d["date"], []).append(sl["slot_id"])

    elif s["action"] == "shift_times":
        start = str(cons.get("shift_start_to") or "12:00")[:5]
        for d in target_days:
            movable = [sl for sl in d["slots"] if sl["item"].get("kind") not in ("arrival", "transfer", "checkin")]
            if not movable:
                continue
            first = min(sl["time"] for sl in movable)
            delta = _mins(start) - _mins(first)
            if delta <= 0:
                continue
            kept = []
            for sl in d["slots"]:
                if sl in movable:
                    t = _mins(sl["time"]) + delta
                    if t > 23 * 60 + 30:
                        changed.setdefault(d["date"], []).append(sl["slot_id"])
                        continue
                    sl["time"] = f"{t // 60:02d}:{t % 60:02d}"
                    changed.setdefault(d["date"], []).append(sl["slot_id"])
                kept.append(sl)
            d["slots"] = sorted(kept, key=lambda x: x["time"])

    elif s["action"] == "remove_slots":
        for d in target_days:
            before = len(d["slots"])
            d["slots"] = [sl for sl in d["slots"] if sl["slot_id"] not in set(s.get("slot_ids") or [])]
            if len(d["slots"]) != before:
                changed[d["date"]] = list(s.get("slot_ids") or [])

    if s.get("notice"):
        for d in target_days:
            d["notice"] = s["notice"]
            changed.setdefault(d["date"], [])

    if changed:
        B.polish(new_days, trip, trip["destination"]["name"], (board.get("budget") or {}).get("text", ""), only_dates=set(changed))
    rr = db.refine_requests.find_one({"_id": state["refine_id"]})
    assembled = assemble_board(trip, new_days, facts, board.get("gaps") or [], board)
    assembled.update(title=board["title"], summary=board.get("summary", ""), last_refine={"id": state["refine_id"], "instruction": rr["instruction"],
                                                                                         "source": rr["source"], "changed": changed})
    res = B.write_board(trip, assembled, plan_status=False, reason=f"refine:{rr['source']}:{state['refine_id']}")
    db.refine_requests.update_one({"_id": state["refine_id"], "status": "claimed"},
                                  {"$set": {"status": "done", "result_board_version": res["version"], "changed": changed, "finished_at": datetime.now(UTC)}})
    log.info("refine %s → board v%d, changed %s", state["refine_id"], res["version"], changed)
    return {"changed": changed, "result": res, "timings": {**(state.get("timings") or {}), "replan": round(time.time() - t0, 1)}}


def _mins(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _ctx(trip: dict, facts: dict) -> dict:
    db = get_db()
    transfer_ids = [l["_id"] for l in db.links.find({"trip_id": trip["_id"], "item_ref": "transfer:airport", "validation.ok": True}, {"_id": 1})]
    return {"refs": B.links_by_ref(trip["_id"]), "band": trip["profile"].get("budget_band") or "mid", "city": trip["destination"]["name"],
            "stay": facts["stay"], "arrival": {"leg": None}, "airport_id": facts.get("airport_id"), "flight_links": [],
            "transfer_ids": transfer_ids, "transfer_urls": [l["url"] for l in db.links.find({"_id": {"$in": transfer_ids}})]}


def _route(state: RefineState) -> str:
    s = state["scope"]
    return "re_research" if (s.get("needs_research") or s.get("category_key") == "beach") and s["action"] == "retheme_day" else "replan"


@lru_cache(maxsize=1)
def graph():
    g = StateGraph(RefineState)
    g.add_node("claim", claim)
    g.add_node("scope", scope)
    g.add_node("re_research", re_research)
    g.add_node("replan", replan)
    g.add_edge(START, "claim")
    g.add_edge("claim", "scope")
    g.add_conditional_edges("scope", _route, {"re_research": "re_research", "replan": "replan"})
    g.add_edge("re_research", "replan")
    g.add_edge("replan", END)
    return g.compile(checkpointer=checkpointer())


def run_refine(refine_id: str) -> dict:
    cfg = {"configurable": {"thread_id": f"refine:{refine_id}"}, "tags": ["track:3", "refine"], "run_name": "wander-refine"}
    g = graph()
    snap = g.get_state(cfg)
    try:
        if snap and snap.next:
            return g.invoke(None, cfg)
        return g.invoke({"refine_id": refine_id}, cfg)
    except Exception as exc:
        get_db().refine_requests.update_one({"_id": refine_id, "status": "claimed"}, {"$set": {"status": "failed", "error": str(exc)[:500]}})
        raise
