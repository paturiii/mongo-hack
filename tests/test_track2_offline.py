"""Fast offline checks for Track 2 logic (no network, no Mongo). Run: .venv/bin/pytest -q"""
import os

os.environ.setdefault("MONGODB_URI", "mongodb://unused")  # modules under test never connect

from track2_app.auth import hash_password, verify_password
from track2_app.intake import slots
from track2_app.memory.store import usual_note


def test_question_engine_matches_ac():
    # "Lisbon in March, food + nightlife, flying TP1351 from London"
    known = {"interests": ["food", "nightlife"], "origin": "London"}
    asked = slots.pick(known, set(), batch=1)
    assert {"budget_band", "pace", "stay_vibe", "nightlife_intensity"} <= set(asked)
    assert "interests" not in asked and "origin" not in asked
    assert len(asked) <= slots.MAX_PER_BATCH


def test_memory_prefill_skips_questions():
    known = {"interests": ["food"], "pace": "relaxed", "budget_band": "mid"}
    asked = slots.pick(known, set(), batch=1)
    assert "pace" not in asked and "budget_band" not in asked
    assert "nightlife_intensity" not in asked  # not relevant without nightlife


def test_batch_two_only_reasks_required_gaps():
    known = {"interests": ["food"], "budget_band": "mid", "pace": "relaxed", "travelers": 2}
    assert slots.pick(known, {"dates", "stay_vibe"}, batch=2) == ["dates"]


def test_every_question_has_why_and_options_or_input_type():
    for sid in slots.SLOTS:
        q = slots.question(sid)
        assert q["why"] and (q["options"] or q["type"] in ("date_range", "number", "free"))


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)


def test_usual_note_orders_pace_then_budget():
    assert usual_note({"budget_band": "mid", "pace": "relaxed"}) == "using your usual: relaxed, mid-range"
    assert usual_note({}) is None
