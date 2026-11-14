"""Run one research agent inline (dev helper).

    python scripts/run_job.py places --city Lisbon --theme sights
    python scripts/run_job.py opinions --city Lisbon --theme food
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from track3_research.agents import agent_for  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind")
    ap.add_argument("--city", default="Lisbon")
    ap.add_argument("--trip", default=None)
    ap.add_argument("--params", default="{}", help="extra params as JSON")
    ap.add_argument("--theme", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    params = {"city": a.city, **json.loads(a.params)}
    if a.theme:
        params["theme"] = a.theme
    t = time.time()
    res = agent_for(a.kind)({"_id": "job_inline", "trip_id": a.trip, "kind": a.kind, "params": params}).run()
    print(json.dumps({"refs": len(res["result_refs"]), "stats": res["stats"], "secs": round(time.time() - t, 1)}, default=str))


if __name__ == "__main__":
    main()
