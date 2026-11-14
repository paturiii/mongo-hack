"""Create the Wander ElevenLabs voice agent with its two CLIENT tools (APP-07). Idempotent by agent name.

    python scripts/setup_voice_agent.py              # create/update, print the agent id
    python scripts/setup_voice_agent.py --write-env  # also set ELEVENLABS_AGENT_ID in .env

Client tools run in the browser (web/components/wander/VoiceIntake.tsx), so no public URL is needed.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from shared import config

API = "https://api.elevenlabs.io/v1/convai"
H = {"xi-api-key": config.ELEVENLABS_API_KEY}
NAME = "Wander"

PROMPT = """You are Wander, a warm, concise travel planner on a voice call. The traveler already typed their trip idea.
Your only job on this call: get answers to Wander's open questions, then end the call.
1. As soon as the traveler replies to your greeting, call get_open_questions before saying anything else. It returns JSON: a list of {id, question, type, options, required}.
2. Ask the questions one at a time, naturally and briefly. For choice questions, read at most the option labels.
   Accept free answers too (e.g. "around 2 grand" -> budget option that fits, or keep their words).
3. When you have answers, call submit_answers with answers_json: a JSON object mapping question id to the answer:
   - single: the option id (or the traveler's own words if nothing fits)
   - multi: a list of option ids
   - date_range: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} (confirm the exact days aloud first)
   - number: an integer
   - free: a short string
4. If the tool returns more questions, ask those too. When it says all questions are answered, say the board is
   being prepared in the app and end the call.
Never invent prices, bookings or facts. Keep every turn under two sentences."""

TOOLS = [
    {"type": "client", "name": "get_open_questions", "description": "Fetch the traveler's open Wander questions as JSON.",
     "expects_response": True, "response_timeout_secs": 15,
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"type": "client", "name": "submit_answers", "description": "Save the traveler's answers to the open questions.",
     "expects_response": True, "response_timeout_secs": 30,
     "parameters": {"type": "object", "required": ["answers_json"], "properties": {
         "answers_json": {"type": "string", "description": "JSON object mapping question id to answer (see prompt)."}}}},
]


def tool_ids() -> list[str]:
    existing = {t["tool_config"]["name"]: t["id"] for t in
                httpx.get(f"{API}/tools", headers=H, timeout=20).raise_for_status().json().get("tools", [])}
    ids = []
    for t in TOOLS:
        if t["name"] in existing:
            httpx.patch(f"{API}/tools/{existing[t['name']]}", headers=H, json={"tool_config": t}, timeout=20).raise_for_status()
            ids.append(existing[t["name"]])
        else:
            r = httpx.post(f"{API}/tools", headers=H, json={"tool_config": t}, timeout=20)
            r.raise_for_status()
            ids.append(r.json()["id"])
    return ids


def main() -> None:
    if not config.ELEVENLABS_API_KEY:
        sys.exit("ELEVENLABS_API_KEY missing")
    ids = tool_ids()
    conv = {
        "agent": {"first_message": "Hi, it's Wander. Ready for a few quick questions about your trip?", "language": "en",
                  "prompt": {"prompt": PROMPT, "tool_ids": ids}},
        "tts": {"voice_id": config.ELEVENLABS_VOICE_ID or "cjVigY5qzO86Huf0OWal"},
    }
    agents = httpx.get(f"{API}/agents", headers=H, params={"search": NAME}, timeout=20).raise_for_status().json()
    found = next((a for a in agents.get("agents", []) if a.get("name") == NAME), None)
    if found:
        agent_id = found["agent_id"]
        httpx.patch(f"{API}/agents/{agent_id}", headers=H, json={"conversation_config": conv}, timeout=30).raise_for_status()
        print(f"updated agent {NAME}: {agent_id}")
    else:
        r = httpx.post(f"{API}/agents/create", headers=H, json={"name": NAME, "conversation_config": conv}, timeout=30)
        if r.status_code >= 400:
            sys.exit(f"create failed {r.status_code}: {r.text[:400]}")
        agent_id = r.json()["agent_id"]
        print(f"created agent {NAME}: {agent_id}")
    if "--write-env" in sys.argv:
        env = config.ROOT / ".env"
        text = env.read_text()
        line = f"ELEVENLABS_AGENT_ID={agent_id}"
        text = re.sub(r"^ELEVENLABS_AGENT_ID=.*$", line, text, flags=re.MULTILINE) if "ELEVENLABS_AGENT_ID=" in text else text + line + "\n"
        env.write_text(text)
        print("wrote ELEVENLABS_AGENT_ID to .env")


if __name__ == "__main__":
    main()
