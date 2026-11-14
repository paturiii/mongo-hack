"""Load the isolated Lisbon Track 1 demo documents into Atlas; never wipe collections."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import ROOT, Settings
from shared.http import now


def load(db, city="lisbon"):
    folder = ROOT / "fixtures" / city
    for path in folder.glob("*.json"):
        collection = {"trip": "trips", "board": "boards", "user": "users"}.get(path.stem, path.stem)
        documents = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(documents, dict):
            documents = [documents]
        for doc in documents:
            if collection == "users":
                doc["created_at"] = now()
                doc["password_hash"] = "!fixture-login-disabled"
            old = db[collection].find_one({"_id": doc["_id"]})
            if old and not old.get("fixture"):
                raise RuntimeError("Refusing to overwrite non-fixture document in " + collection)
            doc["fixture"] = True
            doc["updated_at"] = now()
            db[collection].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    db.places.create_index([("geo", "2dsphere")])
    db.auth_sessions.create_index("expires_at", expireAfterSeconds=0)
    print("OK Lisbon fixtures loaded (synthetic trip, real places; no personal data)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="lisbon", choices=["lisbon"])
    args = parser.parse_args()
    client, db = Settings.load().connect()
    try:
        load(db, args.city)
    finally:
        client.close()
