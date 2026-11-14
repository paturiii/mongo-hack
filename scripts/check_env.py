"""Verify required env + Mongo connectivity + validators + search index status. Run first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config

ok = True


def report(name: str, good: bool, note: str = "") -> None:
    global ok
    ok &= good
    print(f"{'✅' if good else '❌'} {name}{' — ' + note if note else ''}")


for k in config.REQUIRED:
    report(k, bool(config.env(k)), "" if config.env(k) else "missing in .env")

try:
    from pymongo import MongoClient
    c = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
    c.admin.command("ping")
    report("mongo ping", True, f"server {c.server_info()['version']}")
    d = c[config.MONGODB_DB]
    infos = {i["name"]: i for i in d.list_collections()}
    missing = [n.stem for n in (config.ROOT / "shared" / "schemas").glob("*.json")
               if "validator" not in infos.get(n.stem, {}).get("options", {})]
    report("$jsonSchema validators", not missing, f"run scripts/setup_db.py (missing: {missing})" if missing else "")
    for coll in ("opinions", "places", "memories", "cities"):
        try:
            st = {i["name"]: i.get("status") for i in d[coll].list_search_indexes()}
            print(f"   search indexes {coll}: {st or 'none'}")
        except Exception as e:  # noqa: BLE001
            print(f"   search indexes {coll}: n/a ({type(e).__name__})")
except Exception as e:  # noqa: BLE001
    report("mongo ping", False, f"{type(e).__name__} — {config.FAIL_MSG}")

sys.exit(0 if ok else 1)
