"""R-01 AC: 50 fixture jobs processed exactly once across 2 workers.

    python scripts/test_workers.py
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import get_db

TRIP = "trip_workertest"
KINDS = ["opinions", "places", "stay", "transit", "getting_there", "tickets", "nightlife", "restaurants", "link_resolve"]


def main() -> int:
    db = get_db()
    db.research_jobs.delete_many({"trip_id": TRIP})
    t0 = datetime.now(UTC)
    db.research_jobs.insert_many([
        {"_id": f"job_wt_{i:03d}", "trip_id": TRIP, "kind": KINDS[i % len(KINDS)], "params": {"city": "Lisbon"},
         "status": "queued", "attempts": 0, "created_at": t0 + timedelta(milliseconds=i)}
        for i in range(50)
    ])
    procs = [
        subprocess.Popen([sys.executable, "-m", "track3_research.workers.run", "--dry-run", "--trip-id", TRIP,
                          "--worker-id", f"w{n}", "--exit-when-idle", "4", "--concurrency", "8"], cwd=ROOT)
        for n in (1, 2)
    ]
    for p in procs:
        p.wait(timeout=180)
    jobs = list(db.research_jobs.find({"trip_id": TRIP}))
    done = [j for j in jobs if j["status"] == "done"]
    once = [j for j in done if len(j.get("executions", [])) == 1 and j["attempts"] == 1]
    by_worker = Counter(j["executions"][0] for j in once)
    ok = len(jobs) == 50 and len(once) == 50 and len(by_worker) == 2
    print(f"jobs={len(jobs)} done={len(done)} exactly_once={len(once)} by_worker={dict(by_worker)} -> {'PASS' if ok else 'FAIL'}")
    db.research_jobs.delete_many({"trip_id": TRIP})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
