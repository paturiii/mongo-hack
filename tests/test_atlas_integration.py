"""Opt-in tests use a new Atlas database per run; no mocks or production data deletion."""

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import WriteError

from scripts.load_fixtures import load
from scripts.setup_db import setup
from scripts.simulate_disruption import KINDS, simulate
from shared.config import ROOT, Settings
from shared.http import now
from track1_disruption.api import router
from track1_disruption.digest import deliver, enqueue
from track1_disruption.fixture_consumer import consume
from track1_disruption.graph import build_graph, resume_choice
from track1_disruption.monitors import poll, recorded
from track1_disruption.resolve import ResolutionConflict, resolve
from track1_disruption.run import claim_watch, persist_signal
from track1_disruption.watchlist import build_watchlist

pytestmark = pytest.mark.skipif(
    os.getenv("WANDER_RUN_INTEGRATION") != "1", reason="Needs Atlas: set WANDER_RUN_INTEGRATION=1"
)


@pytest.fixture(scope="module")
def environment():
    settings = Settings.load()
    settings = Settings(
        {
            **settings.values,
            "WANDER_MODE": "fixtures",
            "MONGODB_DB": settings.get("MONGODB_DB") + "__shiv_test_" + uuid.uuid4().hex[:10],
        }
    )
    client, db = settings.connect()
    setup(db)
    load(db)
    # Verify the same time-series and index initialization is repeatable.
    setup(db)
    yield settings, db
    # Retain the isolated database as verification evidence; no shared collections are cleared.
    client.close()


def test_watch_set_and_recorded_monitors(environment, monkeypatch):
    settings, db = environment
    board = db.boards.find_one({"_id": "b_lisbon"})
    first = build_watchlist(db, board)
    assert set(first) == set(build_watchlist(db, board))
    assert len(first) == db.watches.count_documents({"active": True})

    def no_network(*args, **kwargs):
        raise AssertionError("Recorded monitors must not use HTTP")

    monkeypatch.setattr("shared.http.CachedHTTP.request", no_network)
    watches = list(db.watches.find({"active": True}))
    assert {w["kind"] for w in watches} >= {
        "flight",
        "weather",
        "area",
        "venue",
        "event",
        "link",
        "price",
    }
    for watch in watches:
        result = poll(db, settings, watch)
        assert result["fixture"]
        assert result["evidence"][0]["source_url"].startswith("https://")
    changed = {**board, "version": 2, "days": board["days"][:1], "bookings": []}
    retained = build_watchlist(db, changed)
    assert len(retained) < len(first)
    assert db.watches.count_documents({"active": True}) == len(retained)
    build_watchlist(db, board)


def test_database_rejects_invalid_disruption(environment):
    _, db = environment
    with pytest.raises(WriteError):
        db.disruptions.insert_one({"_id": "invalid"})


def test_live_link_404_and_currency_scoped_price_baseline(environment, monkeypatch):
    settings, db = environment
    settings = Settings({**settings.values, "WANDER_MODE": "seeded"})
    link_watch = db.watches.find_one({"trip_id": "trip_lisbon", "kind": "link"})
    monkeypatch.setattr(
        "shared.http.CachedHTTP.request",
        lambda *a, **kw: {
            "status_code": 404,
            "ok": False,
            "url": "https://www.lisboastorycentre.pt/",
        },
    )
    assert poll(db, settings, link_watch)["kind"] == "dead_link"
    assert db.links.find_one({"_id": "link_museum"})["validation"]["ok"] is False
    price_watch = db.watches.find_one({"trip_id": "trip_lisbon", "kind": "price"})
    for index in range(7):
        db.price_snapshots.insert_one(
            {
                "ts": now() - timedelta(days=index + 1),
                "meta": {
                    "link_id": "link_museum",
                    "provider": "official",
                    "kind": "monument_ticket",
                },
                "amount": 8,
                "currency": "EUR",
            }
        )
    db.price_snapshots.insert_one(
        {
            "ts": now() - timedelta(hours=1),
            "meta": {"link_id": "link_museum", "provider": "official", "kind": "monument_ticket"},
            "amount": 10000,
            "currency": "JPY",
        }
    )
    db.links.update_one(
        {"_id": "link_museum"},
        {
            "$set": {
                "price_hint": {
                    "amount": 12,
                    "currency": "EUR",
                    "as_of": "2026-11-14T12:00:00Z",
                }
            }
        },
    )
    assert poll(db, settings, price_watch)["kind"] == "price_spike"
    count = db.price_snapshots.count_documents({})
    assert poll(db, settings, price_watch) is None
    assert db.price_snapshots.count_documents({}) == count
    db.links.update_one(
        {"_id": "link_museum"},
        {
            "$set": {
                "price_hint": {
                    "amount": 6,
                    "currency": "EUR",
                    "as_of": "2026-11-14T13:00:00Z",
                }
            }
        },
    )
    assert poll(db, settings, price_watch) is None
    assert db.alerts.find_one({"title": "A watched price dropped"})["source_url"]


def test_simulator_all_kinds_under_30_seconds_and_dedupes(environment):
    settings, db = environment
    for kind in KINDS:
        result = simulate(db, settings, "trip_lisbon", kind)
        assert result["elapsed_seconds"] <= 30
        alerts = list(db.alerts.find({"disruption_id": result["disruption_id"]}))
        assert {a["channel"] for a in alerts} == {"board", "email", "voice"}
        assert all(a["status"] == "sent" for a in alerts), [
            (a["channel"], a.get("last_error")) for a in alerts
        ]
        assert next(a for a in alerts if a["channel"] == "voice")["audio_url"]
        doc = db.disruptions.find_one({"_id": result["disruption_id"]})
        assert 2 <= len(doc["options"]) <= 3
        assert any(action.get("url") for option in doc["options"] for action in option["actions"])
        if kind == "strike":
            assert result["affected"]["days"] == ["2026-03-14"]
            assert result["affected"]["slot_ids"] == ["slot_2_0", "slot_2_1", "slot_2_3"]
            assert result["severity"] >= 3
        before = db.signals.count_documents({})
        repeated = simulate(db, settings, "trip_lisbon", kind)
        assert repeated["disruption_id"] == result["disruption_id"]
        assert db.signals.count_documents({}) == before


def test_atomic_resolve_ownership_double_submit_checkpoint_and_partial_replan(environment):
    settings, db = environment
    result = simulate(db, settings, "trip_lisbon", "weather")
    disruption_id = result["disruption_id"]
    config = {"configurable": {"thread_id": disruption_id}}
    assert build_graph(db, settings).get_state(config).interrupts
    with pytest.raises(PermissionError):
        resolve(db, disruption_id, "replan", "another-user")
    with pytest.raises(ValueError):
        resolve(db, disruption_id, "bad-option", "user_lisbon_fixture")
    assert db.disruptions.find_one({"_id": disruption_id})["status"] == "notified"
    before = db.boards.find_one({"_id": "b_lisbon"})

    def choose():
        try:
            return resolve(db, disruption_id, "replan", "user_lisbon_fixture")
        except ResolutionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: choose(), range(2)))
    assert sum(row is not None for row in results) == 1
    winner = next(row for row in results if row)
    assert db.refine_requests.count_documents({"disruption_id": disruption_id}) == 1
    assert db.memories.count_documents({"_id": "feedback_" + disruption_id}) == 1
    assert db.disruption_audit.count_documents({"disruption_id": disruption_id}) == 1
    resume_choice(db, settings, disruption_id, "replan")
    assert not build_graph(db, settings).get_state(config).interrupts
    consume(db, settings, winner["refine_request_id"])
    after = db.boards.find_one({"_id": "b_lisbon"})
    assert after["version"] == before["version"] + 1
    for old_day, new_day in zip(before["days"], after["days"], strict=True):
        if old_day["date"] != "2026-03-14":
            assert old_day == new_day
    assert db.refine_requests.find_one({"_id": winner["refine_request_id"]})["status"] == "done"


def test_two_real_processes_claim_thirty_watches_once_and_dedupe(environment):
    settings, db = environment
    db.watches.update_many({}, {"$set": {"next_check_at": now() + timedelta(days=1)}})
    for index in range(30):
        db.watches.insert_one(
            {
                "_id": f"queue_watch_{index}",
                "trip_id": "trip_queue",
                "kind": "weather",
                "target_key": str(index),
                "target": {"date": "2026-03-14"},
                "next_check_at": now(),
                "interval_s": 300,
                "active": True,
            }
        )
    env = {**os.environ, **settings.values}
    processes = [
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "verify_queue_worker.py")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for process in processes:
        _stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr.decode(errors="replace")[-1500:]
    assert db.test_claims.count_documents({}) == 30
    assert len(db.test_claims.distinct("pid")) == 2
    assert db.signal_receipts.count_documents({"signal.meta.trip_id": "trip_queue"}) == 30
    watch = db.watches.find_one({"_id": "queue_watch_0"})
    payload = recorded("weather", watch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: persist_signal(db, watch, payload), range(16)))
    assert len(set(ids)) == 1
    assert db.signal_receipts.count_documents({"signal.meta.watch_id": watch["_id"]}) == 1
    # A stale lease can be reclaimed, but a live lease cannot.
    db.watches.update_one(
        {"_id": watch["_id"]},
        {"$set": {"next_check_at": now(), "lease_until": now() - timedelta(seconds=1)}},
    )
    assert claim_watch(db, "recovery")["_id"] == watch["_id"]
    assert claim_watch(db, "competing") is None


def test_api_auth_and_demo_resolve(environment):
    settings, db = environment
    app = FastAPI()
    app.include_router(router)
    app.state.settings, app.state.db = settings, db
    with TestClient(app) as client:
        board = client.get("/api/boards/lisbon-fixture-demo")
        assert board.status_code == 200
        assert client.get("/api/trips/trip_lisbon/disruptions").status_code == 401
        assert (
            client.post("/api/demo/session", headers={"origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.post("/api/demo/session").status_code == 200
        assert client.get("/api/trips/trip_lisbon/board").status_code == 200
        disruption = db.disruptions.find_one({"kind": "flight_cancel"})
        response = client.post(
            f"/api/disruptions/{disruption['_id']}/resolve", json={"option_id": "keep"}
        )
        assert response.status_code == 200
        assert (
            client.post(
                f"/api/disruptions/{disruption['_id']}/resolve", json={"option_id": "keep"}
            ).status_code
            == 409
        )
        assert client.get("/api/trips/not-my-trip/disruptions").status_code == 404


def test_pretrip_digest_has_three_checks(environment):
    settings, db = environment
    ids = enqueue(db, force_fixture=True)
    assert ids
    doc = db.pretrip_digests.find_one({"_id": ids[0]})
    assert len(doc["checks"]) >= 3
    assert all(row["source_url"] and row["fetched_at"] for row in doc["checks"])
    deliver(db, settings)
    assert db.pretrip_digests.find_one({"_id": ids[0]})["status"] == "sent"
