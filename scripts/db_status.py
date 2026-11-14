"""Print collections, doc counts, validators and indexes (read-only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config
from shared.db import db

d = db()
print(f"db={config.MONGODB_DB}")
for info in sorted(d.list_collections(), key=lambda i: i["name"]):
    name = info["name"]
    if name.startswith("system."):
        continue
    opts = info.get("options", {})
    kind = "timeseries" if "timeseries" in opts else ("view" if info.get("type") == "view" else "coll")
    n = d[name].estimated_document_count()
    idx = [ix["name"] for ix in d[name].list_indexes()] if kind != "view" else []
    print(f"{name:18} {kind:10} docs={n:<6} validator={'yes' if 'validator' in opts else 'no':3} indexes={idx}")
