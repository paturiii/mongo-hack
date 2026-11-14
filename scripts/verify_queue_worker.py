"""Subprocess helper for the real two-process watch-claim integration test."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import Settings
from shared.http import now
from track1_disruption.run import claim_watch, run_watch

if __name__ == "__main__":
    settings = Settings.load()
    client, db = settings.connect()
    if "__shiv_test_" not in db.name:
        raise RuntimeError("Verification worker requires an isolated test database")
    try:
        while watch := claim_watch(db, str(os.getpid())):
            db.test_claims.insert_one({"_id": watch["_id"], "pid": os.getpid(), "at": now()})
            run_watch(db, settings, watch)
    finally:
        client.close()
