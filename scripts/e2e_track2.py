"""Track 2 end-to-end check against a running gateway + dev stub planner (real LLM, real Atlas).

    MONGODB_DB=wander_dev uvicorn track2_app.gateway.app:app --port 8000 &
    MONGODB_DB=wander_dev python scripts/dev_stub_planner.py &
    MONGODB_DB=wander_dev python scripts/e2e_track2.py
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = os.environ.get("WANDER_API", "http://localhost:8000")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = "") -> bool:
    results.append((name, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + note if note else ''}", flush=True)
    return ok


def ids(view: dict) -> list[str]:
    return [i["id"] for i in (view.get("questions") or {}).get("items", [])]


def wait_board(c: httpx.Client, sid: str, timeout: float = 40) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/sessions/{sid}").json()
        if v.get("board"):
            return v
        time.sleep(1)
    return v


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)
    email, pw = f"e2e_{secrets.token_hex(4)}@example.com", "correct horse battery"

    # APP-01: fail fast without Mongo
    env = {**os.environ, "MONGODB_URI": ""}
    p = subprocess.run([sys.executable, "-c", "import track2_app.gateway.app"], check=False, cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    check("APP-01 gateway refuses to boot without MONGODB_URI", p.returncode != 0 and "MONGODB_URI" in p.stderr,
          p.stderr.strip().splitlines()[-1] if p.stderr else "")

    # APP-03: auth
    r = c.post("/auth/signup", json={"email": email, "password": pw, "name": "E2E"})
    check("APP-03 signup sets session cookie", r.status_code == 200 and "wander_session" in c.cookies, str(r.status_code))
    check("APP-03 duplicate signup rejected", c.post("/auth/signup", json={"email": email, "password": pw}).status_code == 409)
    anon = httpx.Client(base_url=BASE)
    check("APP-03 /api calls need a session", anon.get("/api/me").status_code == 401)
    check("APP-03 wrong password rejected", anon.post("/auth/login", json={"email": email, "password": "nope-nope"}).status_code == 401)
    me = c.get("/api/me").json()
    user_id = me["id"]

    # APP-04: intake
    t0 = time.time()
    v = c.post("/api/trips", json={"prompt": "5 days in Lisbon in March, I like food and nightlife, flying TP1351 from London"}).json()
    q = ids(v)
    check("APP-04 asks budget, pace, stay vibe, nightlife intensity",
          {"budget_band", "pace", "stay_vibe", "nightlife_intensity"} <= set(q), f"asked {q} in {time.time() - t0:.1f}s")
    check("APP-04 does NOT ask interests or origin", not ({"interests", "origin"} & set(q)))
    check("APP-04 at most 6 questions, each with why + options", len(q) <= 6 and all(
        i["why"] and (i["options"] or i["type"] in ("date_range", "number", "free")) for i in v["questions"]["items"]))
    legs = v["trip"].get("legs") or []
    check("APP-04 flight leg saved", any((l.get("number") or "").replace(" ", "") == "TP1351" for l in legs), json.dumps(legs))
    check("APP-04 dates not guessed from 'March'", not v["trip"].get("dates"), str(v["trip"].get("dates")))
    check("APP-04 destination geocoded", ((v["trip"].get("destination") or {}).get("geo") or {}).get("type") == "Point")
    sid = v["session"]["id"]
    answers = {"dates": {"start": "2027-03-12", "end": "2027-03-17"}, "budget_band": "mid", "pace": "relaxed",
               "travelers": 2, "stay_vibe": "historic", "nightlife_intensity": "big_night_out"}
    v = c.post(f"/api/sessions/{sid}/answers", json={"answers": {k: a for k, a in answers.items() if k in q}}).json()
    check("APP-04 answers resume graph → researching", v["session"]["status"] in ("researching", "ready")
          and not v.get("questions"), v["session"]["status"])
    check("APP-04 trip handed to Track 3 (ready_for_research or later)",
          v["trip"]["status"] in ("ready_for_research", "researching", "planned"), v["trip"]["status"])
    check("APP-04 double answer rejected", c.post(f"/api/sessions/{sid}/answers", json={"answers": {}}).status_code == 409)

    # progress + board (via dev stub planner)
    v = wait_board(c, sid)
    check("APP-05 progress counts from research_jobs", (v.get("progress") or {}).get("jobs_total", 0) > 0,
          json.dumps({k: v["progress"][k] for k in ("jobs_done", "jobs_total", "opinions")}) if v.get("progress") else "")
    board = v.get("board")
    check("APP-06 board ready for the session", bool(board), str(board))
    if board:
        slug = board["slug"]
        bv = c.get(f"/api/boards/{slug}").json()
        check("APP-06 board is public-by-link", anon.get(f"/api/boards/{slug}").status_code == 200)
        slots_ok = all(sl["item"].get("geo") for d in bv["board"]["days"] for sl in d["slots"])
        check("APP-06 every slot has geo for the map + opinion chips",
              slots_ok and any(sl["item"].get("opinion_chips") for d in bv["board"]["days"] for sl in d["slots"]))

        events: list[tuple[float, dict]] = []
        connected = threading.Event()

        def listen():
            with httpx.stream("GET", f"{BASE}/api/boards/{slug}/events", timeout=30) as s:
                for line in s.iter_lines():
                    if line.startswith("data: "):
                        events.append((time.time(), json.loads(line[6:])))
                        connected.set()
                        if len(events) >= 2:
                            return

        th = threading.Thread(target=listen, daemon=True)
        th.start()
        connected.wait(timeout=15)  # first snapshot = stream is open
        link_id = bv["board"]["bookings"][1]["link_id"]
        # write straight to Mongo (like Track 3 would) so we time only change stream → SSE
        from shared.db import db as _db
        _db().command("ping")  # warm the client so connection setup isn't timed
        t1 = time.time()
        _db().boards.update_one({"share_slug": slug}, {"$set": {"summary": f"live check {t1}"}})
        th.join(timeout=10)
        dt = (events[-1][0] - t1) if len(events) >= 2 else 99
        live_ok = len(events) >= 2 and events[-1][1]["board"]["summary"] == f"live check {t1}"
        check("APP-06 change stream pushes board update within 2s", live_ok and dt < 2.0, f"{dt:.2f}s, {len(events)} events")
        r = c.post(f"/api/boards/{slug}/bookings/{link_id}", json={"booked": True}).json()
        check("APP-06 mark booked updates checklist progress", r["board"]["checklist_progress"]["booked"] == 2,
              json.dumps(r["board"]["checklist_progress"]))
        check("APP-06 non-owner cannot mark booked",
              anon.post(f"/api/boards/{slug}/bookings/{link_id}", json={"booked": False}).status_code == 401)
        ics = c.get(f"/api/boards/{slug}/ics")
        check("APP-11 .ics export", ics.status_code == 200 and ics.text.count("BEGIN:VEVENT") >= 10
              and "https://" in ics.text, f"{ics.text.count('BEGIN:VEVENT')} events")
        rv = c.post(f"/api/sessions/{sid}/refine", json={"instruction": "make day 3 a beach day"}).json()
        time.sleep(3)
        b2 = c.get(f"/api/boards/{slug}").json()["board"]
        check("refine → refine_requests → board v2 (stub)", b2["version"] >= 2 and rv["session"]["status"] == "refining",
              f"v{b2['version']}")

    # APP-08: memory
    mem = c.get("/api/memory").json()
    prefs = {m["key"]: m["value"] for m in mem if m["kind"] == "pref"}
    check("APP-08 answers saved as prefs", prefs.get("pace") == "relaxed" and prefs.get("budget_band") == "mid", json.dumps(prefs))
    check("APP-08 refine saved as feedback", any(m["kind"] == "feedback" for m in mem))
    v2 = c.post("/api/trips", json={"prompt": "A long weekend in Porto, mostly food"}).json()
    q2 = ids(v2)
    greet = next((m["content"] for m in v2["session"]["messages"] if m["role"] == "assistant"), "")
    check("APP-08 trip 2 skips pace + budget", not ({"pace", "budget_band"} & set(q2)), f"asked {q2}")
    check("APP-08 trip 2 says it used your usual", "relaxed" in greet.lower() and "mid" in greet.lower(), greet)
    pace_mem = next(m for m in mem if m["kind"] == "pref" and m["key"] == "pace")
    check("APP-08 forget a pref", c.delete(f"/api/memory/{pace_mem['_id']}").status_code == 200)
    v3 = c.post("/api/trips", json={"prompt": "Four days in Rome"}).json()
    check("APP-08 forgotten pref is asked again", "pace" in ids(v3) and "budget_band" not in ids(v3), f"asked {ids(v3)}")
    ep = c.post(f"/api/trips/{v['trip']['_id']}/feedback", json={"text": "Loved Alfama fado, Sintra felt rushed"})
    check("APP-08 trip episode saved", ep.status_code == 200)

    # APP-09: autocomplete
    ac = [x["name"] for x in c.get("/api/autocomplete", params={"q": "lis"}).json()]
    check("APP-09 'lis' → Lisbon before Lisburn", ac[:1] == ["Lisbon"] and "Lisburn" in ac, str(ac))

    # APP-07: voice signed url
    vs = c.post("/api/voice/session")
    check("APP-07 ElevenLabs signed URL", vs.status_code == 200 and vs.json()["signed_url"].startswith("wss://"),
          str(vs.status_code))
    oq = c.get(f"/api/sessions/{v3['session']['id']}/questions/open").json()
    check("APP-07 open questions for the voice client tool", len(oq.get("items", [])) > 0)

    # APP-10: metering
    from shared.db import db
    from track2_app import usage
    used = usage.used(user_id)
    check("APP-10 LLM calls metered per user", used >= 6, f"{used} calls today")
    db().usage.update_one({"user_id": user_id, "day": time.strftime("%Y-%m-%d", time.gmtime())}, {"$set": {"llm_calls": 10_000}})
    r = c.post("/api/trips", json={"prompt": "Weekend in Paris"})
    check("APP-10 quota exceeded → friendly upsell", r.status_code == 429 and "Pro" in r.json()["detail"])

    c.post("/auth/logout")
    check("APP-03 logout ends the session", c.get("/api/me").status_code == 401)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
