from datetime import UTC, datetime
from unittest.mock import MagicMock

from track1_disruption.digest import enqueue


def test_mongo_timestamp_and_date_string_queue_the_same_digest(monkeypatch):
    db = MagicMock()
    trip = {"_id": "trip_test", "user_id": "user_test", "dates": {"start": "2026-11-15"}}
    db.trips.find.return_value = [trip]
    monkeypatch.setattr("track1_disruption.digest.checks", lambda *_: [])
    at = datetime(2026, 11, 14, tzinfo=UTC)
    string_ids = enqueue(db, at=at)
    trip["dates"]["start"] = datetime(2026, 11, 15, tzinfo=UTC)
    timestamp_ids = enqueue(db, at=at)
    assert len(string_ids) == 1
    assert timestamp_ids == string_ids
