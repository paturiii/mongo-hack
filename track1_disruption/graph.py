"""Resumable signal → impact → playbooks → notify → human choice graph."""

import hashlib
import json
from typing import TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from shared.http import now
from shared.models import Disruption
from track1_disruption.alerts import create_alerts
from track1_disruption.impact import affected
from track1_disruption.playbooks import options


class State(TypedDict, total=False):
    signal: dict
    disruption_id: str
    disruption: dict | None
    choice: str


def identifier(signal):
    return "dis_" + signal["hash"][:32]


def signal_hash(trip_id, watch_id, payload):
    stable = {key: value for key, value in payload.items() if key != "evidence"}
    stable["sources"] = [row["source_url"] for row in payload.get("evidence", [])]
    return hashlib.sha256(
        json.dumps([trip_id, watch_id, stable], sort_keys=True, default=str).encode()
    ).hexdigest()


def build_graph(db, settings):
    def detect(state):
        signal = state["signal"]
        payload = signal["payload"]
        trip = db.trips.find_one({"_id": signal["meta"]["trip_id"]})
        board = db.boards.find_one({"trip_id": trip["_id"]}, sort=[("version", -1)])
        impact, severity = affected(db, trip, board, payload)
        if not any(impact.values()):
            return {"disruption": None}
        doc = Disruption(
            _id=state["disruption_id"],
            trip_id=trip["_id"],
            kind=payload["kind"],
            severity=severity,
            detected_at=now(),
            evidence=payload["evidence"],
            affected=impact,
            options=options(db, settings, trip, payload, impact),
            status="open",
            fixture=payload.get("fixture", False),
        ).model_dump(by_alias=True)
        # Pydantic's HttpUrl values must be strings for BSON, while datetimes remain dates.
        for evidence in doc["evidence"]:
            evidence["source_url"] = str(evidence["source_url"])
        for option in doc["options"]:
            for action in option["actions"]:
                if action.get("url"):
                    action["url"] = str(action["url"])
        db.disruptions.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
        return {"disruption": doc}

    def notify(state):
        create_alerts(db, state["disruption"])
        return {}

    def choose(state):
        choice = interrupt(
            {"disruption_id": state["disruption_id"], "options": state["disruption"]["options"]}
        )
        return {"choice": choice}

    graph = StateGraph(State)
    graph.add_node("detect_impact_options", detect)
    graph.add_node("notify", notify)
    graph.add_node("await_choice", choose)
    graph.add_edge(START, "detect_impact_options")
    graph.add_conditional_edges(
        "detect_impact_options", lambda state: "notify" if state["disruption"] else END
    )
    graph.add_edge("notify", "await_choice")
    graph.add_edge("await_choice", END)
    return graph.compile(
        checkpointer=MongoDBSaver(
            db.client,
            db.name,
            checkpoint_collection_name="disruption_checkpoints",
            writes_collection_name="disruption_checkpoint_writes",
        )
    )


def process_signal(db, settings, signal):
    graph = build_graph(db, settings)
    disruption_id = identifier(signal)
    config = {
        "configurable": {"thread_id": disruption_id},
        "tags": ["track:1"],
        "recursion_limit": 10,
    }
    state = graph.get_state(config)
    if state.values:
        if state.next and not state.interrupts:
            graph.invoke(None, config)
    else:
        graph.invoke({"signal": signal, "disruption_id": disruption_id}, config)
    return db.disruptions.find_one({"_id": disruption_id})


def resume_choice(db, settings, disruption_id, choice):
    graph = build_graph(db, settings)
    config = {"configurable": {"thread_id": disruption_id}, "tags": ["track:1"]}
    state = graph.get_state(config)
    if state.interrupts:
        graph.invoke(Command(resume=choice), config)
