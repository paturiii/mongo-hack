"""Real end-to-end trip through all tracks (no stubs): intake → Track 3 research/plan → board.

    python scripts/smoke_trip.py "5 days in Lisbon in May 2027 from Toronto, food and nightlife, two of us"
"""
import secrets
import sys
import time

import httpx

BASE = "http://localhost:8000"
DEFAULTS = {"budget_band": "mid", "pace": "relaxed", "travelers": 2, "stay_vibe": "historic",
            "nightlife_intensity": "drinks", "interests": ["food", "history"], "dietary": ["none"],
            "origin": "Toronto", "destination": "Lisbon"}


def main(prompt: str, start: str = "2027-05-10", end: str = "2027-05-14") -> int:
    c = httpx.Client(base_url=BASE, timeout=180)
    c.post("/auth/signup", json={"email": f"smoke_{secrets.token_hex(4)}@example.com", "password": "smoke-test-123",
                                 "name": "Smoke"}).raise_for_status()
    t0 = time.time()
    v = c.post("/api/trips", json={"prompt": prompt}).json()
    sid = v["session"]["id"]
    print(f"[{time.time() - t0:5.1f}s] trip {v['trip']['_id']} → {(v['trip'].get('destination') or {}).get('name')}, "
          f"origin {(v['trip'].get('origin') or {}).get('name')}")
    while v.get("questions"):
        items = v["questions"]["items"]
        ans = {i["id"]: ({"start": start, "end": end} if i["type"] == "date_range" else DEFAULTS.get(i["id"]))
               for i in items}
        print(f"[{time.time() - t0:5.1f}s] answering {[i['id'] for i in items]}")
        v = c.post(f"/api/sessions/{sid}/answers", json={"answers": {k: a for k, a in ans.items() if a}}).json()
    last = ""
    while time.time() - t0 < 900:
        v = c.get(f"/api/sessions/{sid}").json()
        p = v.get("progress") or {}
        line = f"trip={v['trip']['status']} jobs={p.get('jobs_done')}/{p.get('jobs_total')}"
        if line != last:
            print(f"[{time.time() - t0:5.1f}s] {line}")
            last = line
        if v.get("board"):
            b = c.get(f"/api/boards/{v['board']['slug']}").json()["board"]
            print(f"[{time.time() - t0:5.1f}s] BOARD http://localhost:3000/b/{v['board']['slug']} — {b['title']}")
            print(f"   {len(b['days'])} days, {sum(len(d['slots']) for d in b['days'])} slots, {len(b['bookings'])} bookings")
            for bk in b["bookings"][:4]:
                print(f"   · {bk['kind']}: {bk['label']}")
            return 0
        time.sleep(5)
    print("timed out waiting for board")
    return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
