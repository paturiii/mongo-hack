import pytest

from shared.config import REQUIRED, Settings
from track1_disruption.graph import signal_hash
from track1_disruption.watchlist import interval


def test_missing_atlas_fails_before_any_network():
    with pytest.raises(RuntimeError, match="MONGODB_URI missing/unreachable"):
        Settings({}).connect()


def test_local_mongo_is_rejected():
    values = dict.fromkeys(REQUIRED, "configured")
    values.update(MONGODB_URI="mongodb://localhost:27017", WANDER_MODE="fixtures")
    with pytest.raises(RuntimeError, match="Atlas"):
        Settings(values).validate()


def test_signal_dedupe_ignores_fetch_timestamp_but_keeps_meaningful_changes():
    original = {
        "kind": "flight_delay",
        "minutes": 180,
        "evidence": [{"source_url": "https://www.flytap.com/", "fetched_at": "one"}],
    }
    repeated = {
        **original,
        "evidence": [{"source_url": "https://www.flytap.com/", "fetched_at": "two"}],
    }
    assert signal_hash("trip", "watch", original) == signal_hash("trip", "watch", repeated)
    assert signal_hash("trip", "watch", original) != signal_hash(
        "trip", "watch", {**original, "minutes": 240}
    )


def test_polling_cadence():
    assert interval("2099-01-01", "flight") == 21600
    assert interval("2026-01-01", "flight") == 300
    assert interval("2026-01-01", "link") == 86400
