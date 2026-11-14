"""Generate due T-48h digests; --fixture forces the labeled Lisbon demo digest."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import Settings
from track1_disruption.digest import deliver, enqueue

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--live-alerts", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    if args.fixture and settings.mode != "fixtures":
        raise SystemExit("Forced demo digests require WANDER_MODE=fixtures")
    client, db = settings.connect()
    try:
        ids = enqueue(db, force_fixture=args.fixture)
        deliver(db, settings, live=args.live_alerts)
        for identifier in ids:
            doc = db.pretrip_digests.find_one({"_id": identifier})
            print(
                {
                    "digest_id": identifier,
                    "checks": len(doc["checks"]),
                    "status": doc["status"],
                    "delivery_mode": doc.get("delivery_mode"),
                    "last_error": doc.get("last_error"),
                }
            )
    finally:
        client.close()
