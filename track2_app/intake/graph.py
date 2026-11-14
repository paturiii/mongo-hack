"""Intake graph (MASTERPLAN §7.1):

    load_memory → understand → gap_analysis ─gaps─► ask ⏸ ─► gap_analysis
                                     │ none
                                     ▼
                              finalize (trip.status=ready_for_research) → write_memory

thread_id = session_id; checkpoints in Mongo (MongoDBSaver), so a session survives restarts.
"""
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from shared import config, llm
from shared.db import client, db
from track2_app import usage
from track2_app.intake import slots
from track2_app.intake.understand import geocode, to_known, understand
from track2_app.memory import store as memory

log = logging.getLogger("wander.intake")


class IntakeState(TypedDict, total=False):
    session_id: str
    user_id: str
    trip_id: str
    prompt: str
    known: dict          # slot -> value (prompt + memory + answers)
    prefilled: dict      # slot -> value that came from memory
    recalled: list       # relevant past episodes (text)
    asked: list          # slot ids asked so far
    batch: int
    pending: list        # slot ids picked for the next batch
    month_hint: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _say(session_id: str, text: str) -> None:
    db().sessions.update_one({"_id": session_id},
                             {"$push": {"messages": {"role": "assistant", "content": text, "at": _now()}}})


# ---------------------------------------------------------------- nodes

def load_memory(s: IntakeState) -> dict:
    prefs = memory.prefs(s["user_id"])
    recalled = [m.get("text") for m in memory.recall(s["user_id"], s["prompt"], k=3) if m.get("text")]
    return {"prefilled": prefs, "recalled": recalled}


def understand_node(s: IntakeState) -> dict:
    u = understand(s["prompt"])
    usage.charge(s["user_id"])
    known = to_known(u)
    # memory fills only slots the prompt didn't settle, and only ones relevant to this trip
    prefilled = {k: v for k, v in (s.get("prefilled") or {}).items()
                 if k in slots.SLOTS and not slots.is_known(known.get(k)) and slots.relevant(k, known)}
    known.update(prefilled)

    dest = geocode(u.destination_city, u.destination_country)
    origin = geocode(known.get("origin")) if known.get("origin") else None
    legs = [{"kind": leg.kind, "carrier": leg.carrier, "number": leg.number, "from": leg.from_city,
             "to": leg.to_city, "date": leg.date} for leg in u.legs]
    trip_set = {"destination": dest, "origin": origin, "legs": legs, "travelers": known.get("travelers"),
                "profile": {"interests": known.get("interests", []), "vibe": known.get("vibe", []),
                            "budget_band": known.get("budget_band"), "pace": known.get("pace"),
                            "dietary": known.get("dietary", []), "dealbreakers": known.get("dealbreakers", []),
                            "nightlife_intensity": known.get("nightlife_intensity"), "mobility": None,
                            "must_sees": known.get("must_sees", [])}}
    db().trips.update_one({"_id": s["trip_id"]}, {"$set": trip_set})

    note = memory.usual_note({k: v for k, v in prefilled.items() if slots.SLOTS[k][0] >= slots.MIN_IMPACT_BATCH1})
    where = dest["name"] if dest else "your trip"
    try:
        greeting = llm.text(
            "You are Wander, a warm, concise travel planner. Reply in 1-2 short sentences, no lists, no emojis. "
            "Acknowledge the trip and say you'll ask a few quick questions. Do NOT ask any question yourself (the app "
            "shows question cards). If a 'memory note' is given, weave it in "
            "naturally (e.g. 'I'll go with your usual: relaxed, mid-range').",
            f"Traveler said: {s['prompt']}\nDestination: {where}\nMemory note: {note or 'none'}\n"
            f"Relevant past trips: {'; '.join(s.get('recalled') or []) or 'none'}", smart=True)
        usage.charge(s["user_id"])
    except Exception as e:  # noqa: BLE001 - greeting is cosmetic
        log.warning("greeting fallback: %s", type(e).__name__)
        greeting = f"{where} — great pick. A few quick questions."
    if note and note.removeprefix("using your usual: ").split(", ")[0].lower() not in greeting.lower():
        greeting += f" (I'm {note}.)"  # memory must always be visible, even if the LLM skipped it
    _say(s["session_id"], greeting)
    return {"known": known, "prefilled": prefilled, "asked": [], "batch": 0, "month_hint": u.month_hint}


def gap_analysis(s: IntakeState) -> dict:
    batch = (s.get("batch") or 0) + 1
    if batch > 2:
        return {"pending": []}
    return {"pending": slots.pick(s["known"], set(s.get("asked") or []), batch)}


def ask(s: IntakeState) -> dict:
    batch = (s.get("batch") or 0) + 1
    qid = f"q_{s['session_id'].removeprefix('ses_')}_{batch}"
    items = [slots.question(sid, hint=s.get("month_hint") if sid == "dates" else None) for sid in s["pending"]]
    db().questions.update_one(
        {"_id": qid},
        {"$setOnInsert": {"_id": qid, "session_id": s["session_id"], "batch": batch, "items": items,
                          "status": "open", "answers": {}, "answered_at": None}},
        upsert=True)
    db().sessions.update_one({"_id": s["session_id"]}, {"$set": {"status": "asking"}})
    answers = interrupt({"questions_id": qid, "slots": s["pending"]})  # ⏸ resumed with Command(resume=answers)
    known = {**s["known"], **{k: v for k, v in (answers or {}).items() if k in slots.SLOTS}}
    return {"known": known, "asked": [*(s.get("asked") or []), *s["pending"]], "batch": batch}


def finalize(s: IntakeState) -> dict:
    k = s["known"]
    profile = {"interests": k.get("interests", []), "vibe": k.get("vibe", []), "budget_band": k.get("budget_band"),
               "pace": k.get("pace"), "dietary": [d for d in k.get("dietary", []) if d != "none"],
               "dealbreakers": k.get("dealbreakers", []), "nightlife_intensity": k.get("nightlife_intensity"),
               "stay_vibe": k.get("stay_vibe"), "stay_type": k.get("stay_type"), "mobility": k.get("mobility"),
               "must_sees": k.get("must_sees") if isinstance(k.get("must_sees"), list) else
               [x.strip() for x in str(k.get("must_sees") or "").split(",") if x.strip()],
               "booking_ahead": k.get("booking_ahead"), "transport_pref": k.get("transport_pref")}
    trip_set: dict = {"profile": profile, "status": "ready_for_research"}
    if slots.is_known(k.get("dates")):
        trip_set["dates"] = {"start": k["dates"]["start"], "end": k["dates"]["end"], "flexible": False}
    if k.get("travelers"):
        trip_set["travelers"] = int(k["travelers"])
    if k.get("origin") and isinstance(k["origin"], str):
        trip_set["origin"] = geocode(k["origin"])
    db().trips.update_one({"_id": s["trip_id"]}, {"$set": trip_set})
    db().sessions.update_one({"_id": s["session_id"]}, {"$set": {"status": "researching"}})
    _say(s["session_id"], "Got it. I'm sending scouts to read what travelers say and to find tickets, stays and "
                          "transport. Your board will fill in as results land.")
    return {}


def write_memory(s: IntakeState) -> dict:
    memory.remember_prefs(s["user_id"], {k: v for k, v in s["known"].items() if k not in (s.get("prefilled") or {})})
    return {}


def _route(s: IntakeState) -> str:
    return "ask" if s.get("pending") else "finalize"


@lru_cache
def graph():
    g = StateGraph(IntakeState)
    g.add_node("load_memory", load_memory)
    g.add_node("understand", understand_node)
    g.add_node("gap_analysis", gap_analysis)
    g.add_node("ask", ask)
    g.add_node("finalize", finalize)
    g.add_node("write_memory", write_memory)
    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "understand")
    g.add_edge("understand", "gap_analysis")
    g.add_conditional_edges("gap_analysis", _route, {"ask": "ask", "finalize": "finalize"})
    g.add_edge("ask", "gap_analysis")
    g.add_edge("finalize", "write_memory")
    g.add_edge("write_memory", END)
    saver = MongoDBSaver(client(), db_name=config.MONGODB_DB, checkpoint_collection_name="intake_checkpoints",
                         writes_collection_name="intake_checkpoint_writes")
    return g.compile(checkpointer=saver)


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def start(session_id: str, user_id: str, trip_id: str, prompt: str) -> None:
    graph().invoke({"session_id": session_id, "user_id": user_id, "trip_id": trip_id, "prompt": prompt},
                   _cfg(session_id))


def resume(session_id: str, answers: dict) -> None:
    graph().invoke(Command(resume=answers), _cfg(session_id))
