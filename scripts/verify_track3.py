"""Check the Track 3 acceptance criteria against the live board for a trip.

    python scripts/verify_track3.py --trip trip_t3_lisbon
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import geo  # noqa: E402
from shared.config import get_db  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def verify(trip_id: str) -> bool:
    db = get_db()
    trip = db.trips.find_one({"_id": trip_id})
    board = db.boards.find_one({"trip_id": trip_id})
    if not board:
        print("no board")
        return False
    print(f"board {board['_id']} v{board['version']} slug={board['share_slug']} — {board['title']}")
    results = []
    items = [(d, s) for d in board["days"] for s in d["slots"]]
    unsourced = [s["item"]["name"] for _, s in items if not s["item"].get("sources")]
    results.append(check("every board item has ≥1 source", not unsourced, f"{len(items)} items" + (f", unsourced={unsourced}" if unsourced else "")))
    link_ids = {b["link_id"] for b in board["bookings"]} | {l for _, s in items for l in s["item"].get("link_ids", [])}
    bad = list(db.links.find({"_id": {"$in": list(link_ids)}, "validation.ok": {"$ne": True}}, {"url": 1}))
    results.append(check("every board link is validated ok", not bad and bool(link_ids), f"{len(link_ids)} links, broken={len(bad)}"))
    radii = []
    for d in board["days"]:
        pts = [s["item"]["geo"] for s in d["slots"] if s["item"].get("kind") == "activity" and s["item"].get("geo")]
        if len(pts) >= 2:
            c = geo.centroid(pts)
            radii.append(max(geo.haversine_m(c, p) for p in pts))
    results.append(check("no day spans more than ~4 km radius", all(r <= 4200 for r in radii), f"max activity radius {max(radii, default=0):.0f} m"))
    closed = []
    for d in board["days"]:
        dd = date.fromisoformat(str(d["date"])[:10])
        for s in d["slots"]:
            pid = s["item"].get("place_id")
            p = db.places.find_one({"_id": pid}, {"hours": 1, "category": 1, "name": 1}) if pid else None
            if p and p["category"] == "museum" and not geo.open_on(p.get("hours"), dd, "museum"):
                closed.append(f"{p['name']} on {dd:%a}")
    results.append(check("museums never on closed days", not closed, ", ".join(closed)))
    d1 = board["days"][0]["slots"]
    arrival = next((s for s in d1 if s["item"].get("kind") == "arrival"), None)
    first_act = next((s for s in d1 if s["item"].get("kind") == "activity"), None)
    ok = arrival is not None and (first_act is None or first_act["time"] > arrival["time"])
    results.append(check("day 1 starts after arrival + transfer", ok, f"arrival {arrival and arrival['time']}, first activity {first_act and first_act['time']}"))
    kinds = {b["kind"] for b in board["bookings"]}
    door = {"flight", "transfer", "stay", "monument_ticket", "restaurant"}
    results.append(check("door-to-door bookings (flight, transfer, stay, tickets, restaurants)", door <= kinds, f"kinds={sorted(kinds)}"))
    multi = sum(1 for _, s in items for c in s["item"].get("opinion_chips", []) if " agree: " in c["text"])
    results.append(check("multi-source consensus chips on board", multi > 0, f"{multi} 'X + Y agree' chips"))
    results.append(check("budget estimate on board", bool((board.get("budget") or {}).get("text")), (board.get("budget") or {}).get("text", "")))
    results.append(check("trip status planned + board_id set", trip["status"] in ("planned", "traveling") and trip.get("board_id") == board["_id"], trip["status"]))
    hist = db.board_versions.count_documents({"trip_id": trip_id})
    print(f"  board_versions archived: {hist}")
    return all(results)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trip", default="trip_t3_lisbon")
    raise SystemExit(0 if verify(ap.parse_args().trip) else 1)
