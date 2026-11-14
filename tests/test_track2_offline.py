"""Fast offline checks for Track 2 logic (no network, no Mongo). Run: .venv/bin/pytest -q"""
import os

os.environ.setdefault("MONGODB_URI", "mongodb://unused")  # modules under test never connect

from track2_app.auth import hash_password, verify_password
from track2_app.intake import slots
from track2_app.memory.store import usual_note


def test_question_engine_matches_ac():
    # "Lisbon in March, food + nightlife, flying TP1351 from London"
    known = {"destination": "Lisbon", "interests": ["food", "nightlife"], "origin": "London"}
    asked = slots.pick(known, set(), batch=1)
    assert {"budget_band", "pace", "stay_vibe", "nightlife_intensity"} <= set(asked)
    assert "interests" not in asked and "origin" not in asked
    assert len(asked) <= slots.MAX_PER_BATCH


def test_memory_prefill_skips_questions():
    known = {"destination": "Porto", "interests": ["food"], "pace": "relaxed", "budget_band": "mid"}
    asked = slots.pick(known, set(), batch=1)
    assert "pace" not in asked and "budget_band" not in asked
    assert "nightlife_intensity" not in asked  # not relevant without nightlife


def test_batch_two_only_reasks_required_gaps():
    known = {"destination": "Tokyo", "origin": "Toronto", "interests": ["food"], "budget_band": "mid",
             "pace": "relaxed", "travelers": 2}
    assert slots.pick(known, {"dates", "stay_vibe"}, batch=2) == ["dates"]


def test_origin_and_destination_are_mandatory_and_asked_first():
    asked = slots.pick({"interests": ["food"]}, set(), batch=1)
    assert asked[:2] == ["destination", "origin"]
    assert slots.SLOTS["origin"][1] and slots.SLOTS["destination"][1]
    assert slots.pick({"interests": ["food"], "budget_band": "mid", "pace": "relaxed", "travelers": 2,
                       "dates": {"start": "2027-05-10", "end": "2027-05-14"}, "destination": "Lisbon"},
                      {"origin"}, batch=2) == ["origin"]


def test_every_question_has_why_and_options_or_input_type():
    for sid in slots.SLOTS:
        q = slots.question(sid, options=[("x", "X")] if sid in ("destination", "origin") else None)  # dynamic
        assert q["why"] and (q["options"] or q["type"] in ("date_range", "number", "free"))


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)


def test_usual_note_orders_pace_then_budget():
    assert usual_note({"budget_band": "mid", "pace": "relaxed"}) == "using your usual: relaxed, mid-range"
    assert usual_note({}) is None
