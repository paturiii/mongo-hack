"""Drive the real ElevenLabs Wander agent over its websocket with TYPED user turns (no mic), handling the two client
tools exactly like the browser does. Proves voice intake end to end: agent → get_open_questions → submit_answers →
our API → graph resumes.

    MONGODB_DB=wander_dev python scripts/voice_smoke.py      # gateway must be running
"""
import asyncio
import json
import os
import secrets
import sys

import httpx
import websockets

BASE = os.environ.get("WANDER_API", "http://localhost:8000")
ANSWER = ("We're going March 12 to March 17, 2027. Mid-range budget, relaxed pace, two of us, "
          "somewhere historic and walkable, and one or two big nights out.")


async def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)
    c.post("/auth/signup", json={"email": f"voice_{secrets.token_hex(4)}@example.com", "password": "voice-test-123",
                                 "name": "Voice"}).raise_for_status()
    v = c.post("/api/trips", json={"prompt": "5 days in Lisbon in March, food and nightlife, flying from London"}).json()
    sid = v["session"]["id"]
    print("asked:", [i["id"] for i in v["questions"]["items"]])
    url = c.post("/api/voice/session").json()["signed_url"]
    submitted = False
    sent_answer = False
    greeted = False
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        try:
            async with asyncio.timeout(120):
                async for raw in ws:
                    m = json.loads(raw)
                    t = m.get("type")
                    if t == "ping":
                        await ws.send(json.dumps({"type": "pong", "event_id": m["ping_event"]["event_id"]}))
                    elif t == "agent_response":
                        text = m["agent_response_event"]["agent_response"]
                        print("agent:", text)
                        if not greeted:
                            greeted = True
                            print("user : Yes, ready.")
                            await ws.send(json.dumps({"type": "user_message", "text": "Yes, ready."}))
                        elif not sent_answer and "?" in text:
                            sent_answer = True
                            print("user :", ANSWER)
                            await ws.send(json.dumps({"type": "user_message", "text": ANSWER}))
                    elif t == "client_tool_call":
                        call = m["client_tool_call"]
                        name, params = call["tool_name"], call.get("parameters", {})
                        if name == "get_open_questions":
                            q = c.get(f"/api/sessions/{sid}/questions/open").json()
                            result = json.dumps([{"id": i["id"], "question": i["prompt"], "type": i["type"],
                                                  "options": i["options"], "required": i["required"]} for i in q["items"]]) \
                                if q["items"] else "No open questions. All questions are answered."
                        else:
                            answers = json.loads(params.get("answers_json") or "{}")
                            print("tool submit_answers:", answers)
                            r = c.post(f"/api/sessions/{sid}/answers", json={"answers": answers})
                            submitted = r.status_code == 200
                            nxt = c.get(f"/api/sessions/{sid}/questions/open").json()
                            result = (f"Saved. Next questions: {json.dumps(nxt['items'])}" if nxt["items"]
                                      else "Saved. All questions answered — say goodbye and end the call.") \
                                if submitted else f"Could not save: {r.text[:200]}"
                        print(f"tool {name} → {result[:120]}")
                        await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": call["tool_call_id"],
                                                  "result": result, "is_error": False}))
                        if submitted and "All questions answered" in result:
                            await asyncio.sleep(4)
                            break
        except TimeoutError:
            print("timed out")
    s = c.get(f"/api/sessions/{sid}").json()
    ok = submitted and s["session"]["status"] in ("researching", "ready") and not s["questions"]
    print(f"{'PASS' if ok else 'FAIL'}  voice intake → session {s['session']['status']}, trip {s['trip']['status']}, "
          f"dates {s['trip'].get('dates')}, travelers {s['trip'].get('travelers')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
