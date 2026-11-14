"""Wander gateway (APP-03 + APP-06/07/08/09/10/11). Run:

    uvicorn track2_app.gateway.app:app --port 8000 --reload
"""
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, EmailStr

from shared import config
from shared.db import adb, db
from track2_app import auth, usage
from track2_app.gateway.views import board_view, jsonable, session_view
from track2_app.intake import graph as intake
from track2_app.intake import slots
from track2_app.memory import store as memory

config.require_mongo()  # fail fast: no Mongo, no app (MASTERPLAN §1.1)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wander.gateway")

app = FastAPI(title="Wander gateway", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[config.WEB_ORIGIN], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

User = Depends(auth.current_user)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- auth

class SignupIn(BaseModel):
    email: EmailStr
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=int(auth.SESSION_TTL.total_seconds()),
                    path="/")


@app.post("/auth/signup")
def signup(body: SignupIn, resp: Response):
    auth.signup(body.email, body.password, body.name)
    user, token = auth.login(body.email, body.password)
    _set_cookie(resp, token)
    return auth.public_user(user)


@app.post("/auth/login")
def login(body: LoginIn, resp: Response):
    user, token = auth.login(body.email, body.password)
    _set_cookie(resp, token)
    return auth.public_user(user)


@app.post("/auth/logout")
def logout(resp: Response, wander_session: str | None = Cookie(default=None)):
    auth.logout(wander_session)
    resp.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=User):
    return {**auth.public_user(user), "usage_today": usage.used(user["_id"]), "quota": config.DAILY_LLM_QUOTA}


@app.get("/api/health")
def health():
    db().command("ping")
    return {"ok": True, "db": config.MONGODB_DB, "mode": config.WANDER_MODE}


# ---------------------------------------------------------------- trips / intake

class StartIn(BaseModel):
    prompt: str


@app.post("/api/trips")
def start_trip(body: StartIn, user=User):
    prompt = body.prompt.strip()
    if len(prompt) < 3:
        raise HTTPException(400, "Tell me a bit about the trip first.")
    usage.check(user["_id"])
    sid, tid = f"ses_{secrets.token_hex(6)}", f"trip_{secrets.token_hex(6)}"
    db().trips.insert_one({"_id": tid, "user_id": user["_id"], "session_id": sid, "origin": None, "destination": None,
                           "dates": None, "travelers": None, "legs": [], "profile": {}, "status": "intake",
                           "board_id": None})
    db().sessions.insert_one({"_id": sid, "user_id": user["_id"], "surface": "native", "thread_id": sid, "trip_id": tid,
                              "status": "asking", "messages": [{"role": "user", "content": prompt, "at": _now()}],
                              "created_at": _now(), "expires_at": _now() + timedelta(days=30)})
    intake.start(sid, user["_id"], tid, prompt)
    return session_view(sid, user["_id"])


@app.get("/api/sessions")
def list_sessions(user=User):
    out = []
    for s in db().sessions.find({"user_id": user["_id"]}).sort("created_at", -1).limit(20):
        trip = db().trips.find_one({"_id": s.get("trip_id")}, {"destination": 1, "dates": 1, "status": 1}) or {}
        board = db().boards.find_one({"trip_id": s.get("trip_id")}, {"share_slug": 1, "title": 1})
        out.append({"id": s["_id"], "status": s["status"], "created_at": s.get("created_at"),
                    "first_message": (s.get("messages") or [{}])[0].get("content", ""),
                    "destination": (trip.get("destination") or {}).get("name"), "trip_status": trip.get("status"),
                    "board": {"slug": board["share_slug"], "title": board.get("title")} if board else None})
    return jsonable(out)


@app.get("/api/sessions/{sid}")
def get_session(sid: str, user=User):
    v = session_view(sid, user["_id"])
    if not v:
        raise HTTPException(404, "Trip not found.")
    return v


def _normalize(items: list[dict], answers: dict) -> dict:
    """Coerce answers to each question's type. Unknown ids are dropped."""
    out = {}
    for it in items:
        v = answers.get(it["id"])
        if v in (None, "", []):
            continue
        t = it["type"]
        if t == "number":
            try:
                v = max(1, int(float(v)))
            except (TypeError, ValueError):
                continue
        elif t == "multi":
            v = [x for x in (v if isinstance(v, list) else [v]) if x]
        elif t == "date_range":
            if not (isinstance(v, dict) and v.get("start") and v.get("end")):
                continue
            v = {"start": str(v["start"])[:10], "end": str(v["end"])[:10]}
        else:
            v = str(v).strip()
        out[it["id"]] = v
    return out


def _describe(items: list[dict], answers: dict) -> str:
    parts = []
    for it in items:
        v = answers.get(it["id"])
        if v is None:
            continue
        labels = {o["id"]: o["label"].split(" — ")[0] for o in it.get("options", [])}
        if isinstance(v, dict):
            txt = f"{v['start']} → {v['end']}"
        elif isinstance(v, list):
            txt = ", ".join(labels.get(x, x) for x in v)
        else:
            txt = labels.get(str(v), str(v))
        parts.append(txt)
    return " · ".join(parts) or "(skipped)"


class AnswersIn(BaseModel):
    answers: dict


@app.post("/api/sessions/{sid}/answers")
def answer_questions(sid: str, body: AnswersIn, user=User):
    s = db().sessions.find_one({"_id": sid, "user_id": user["_id"]})
    if not s:
        raise HTTPException(404, "Trip not found.")
    q = db().questions.find_one({"session_id": sid, "status": "open"}, sort=[("batch", -1)])
    if not q:
        raise HTTPException(409, "No open questions for this trip.")
    usage.check(user["_id"])
    answers = _normalize(q["items"], body.answers)
    missing = [it["prompt"] for it in q["items"] if it.get("required") and it["id"] not in answers]
    if missing and q["batch"] >= 2:
        raise HTTPException(400, f"Please answer: {missing[0]}")
    res = db().questions.update_one({"_id": q["_id"], "status": "open"},
                                    {"$set": {"status": "answered", "answers": answers, "answered_at": _now()}})
    if res.modified_count != 1:
        raise HTTPException(409, "Those questions were already answered.")
    db().sessions.update_one({"_id": sid}, {"$push": {"messages": {"role": "user", "content": _describe(q["items"], answers),
                                                                     "at": _now()}}})
    intake.resume(sid, answers)
    return session_view(sid, user["_id"])


@app.get("/api/sessions/{sid}/questions/open")
def open_questions(sid: str, user=User):
    """Used by the voice agent's client tool get_open_questions."""
    if not db().sessions.find_one({"_id": sid, "user_id": user["_id"]}, {"_id": 1}):
        raise HTTPException(404, "Trip not found.")
    q = db().questions.find_one({"session_id": sid, "status": "open"}, sort=[("batch", -1)])
    return jsonable(q) if q else {"items": [], "status": "none"}


class RefineIn(BaseModel):
    instruction: str


@app.post("/api/sessions/{sid}/refine")
def refine(sid: str, body: RefineIn, user=User):
    s = db().sessions.find_one({"_id": sid, "user_id": user["_id"]})
    if not s:
        raise HTTPException(404, "Trip not found.")
    text = body.instruction.strip()
    if not text:
        raise HTTPException(400, "Say what to change.")
    db().refine_requests.insert_one({"_id": f"rr_{secrets.token_hex(6)}", "trip_id": s["trip_id"], "source": "user",
                                     "disruption_id": None, "instruction": text, "scope": {}, "constraints": {},
                                     "status": "queued", "result_board_version": None, "created_at": _now()})
    db().sessions.update_one({"_id": sid}, {"$set": {"status": "refining"}, "$push": {"messages": {"$each": [
        {"role": "user", "content": text, "at": _now()},
        {"role": "assistant", "content": "On it. I'll update only what that touches; the board refreshes live.",
         "at": _now()}]}}})
    memory.add(user["_id"], f"Asked to change a plan: {text}", kind="feedback", source="refine")
    return session_view(sid, user["_id"])


class FeedbackIn(BaseModel):
    text: str


@app.post("/api/trips/{trip_id}/feedback")
def trip_feedback(trip_id: str, body: FeedbackIn, user=User):
    trip = db().trips.find_one({"_id": trip_id, "user_id": user["_id"]}, {"destination": 1})
    if not trip:
        raise HTTPException(404, "Trip not found.")
    where = (trip.get("destination") or {}).get("name", "a trip")
    m = memory.add(user["_id"], f"{where}: {body.text.strip()}", kind="episode", source="rating")
    return jsonable(m)


# ---------------------------------------------------------------- board (public by link)

@app.get("/api/boards/{slug}")
def get_board(slug: str):
    v = board_view(slug)
    if not v:
        raise HTTPException(404, "Board not found.")
    return v


@app.get("/api/boards/{slug}/events")
async def board_events(slug: str, request: Request):
    """Live board via MongoDB change streams → SSE. Sends a full snapshot on every board/alert change."""
    first = await run_in_threadpool(board_view, slug)
    if not first:
        raise HTTPException(404, "Board not found.")
    trip_id = first["board"]["trip_id"]
    pipeline = [{"$match": {"$or": [
        {"ns.coll": "boards", "fullDocument.share_slug": slug},
        {"ns.coll": "alerts", "fullDocument.trip_id": trip_id},
        {"ns.coll": "disruptions", "fullDocument.trip_id": trip_id},
    ]}}]

    async def gen():
        # Open the change stream BEFORE the snapshot, so a change landing in between is never lost.
        async with await adb().watch(pipeline, full_document="updateLookup", max_await_time_ms=10000) as stream:
            snap = await run_in_threadpool(board_view, slug)
            yield f"event: board\ndata: {json.dumps(snap or first)}\n\n"
            while not await request.is_disconnected():
                change = await stream.try_next()
                if change is None:
                    yield ": keepalive\n\n"
                    continue
                t0 = time.perf_counter()
                snap = await run_in_threadpool(board_view, slug)
                log.info("board %s pushed (%s change, snapshot %.0f ms)", slug, change["ns"]["coll"],
                         (time.perf_counter() - t0) * 1000)
                yield f"event: board\ndata: {json.dumps(snap)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class BookedIn(BaseModel):
    booked: bool


@app.post("/api/boards/{slug}/bookings/{link_id}")
def mark_booked(slug: str, link_id: str, body: BookedIn, user=User):
    b = db().boards.find_one({"share_slug": slug}, {"user_id": 1, "bookings": 1})
    if not b or b["user_id"] != user["_id"]:
        raise HTTPException(404, "Board not found.")
    status = "user_marked_booked" if body.booked else "todo"
    # One pipeline update = one change event: flip the booking, then recount progress from the new array.
    db().boards.update_one({"_id": b["_id"]}, [
        {"$set": {"bookings": {"$map": {"input": "$bookings", "in": {"$cond": [
            {"$eq": ["$$this.link_id", link_id]}, {"$mergeObjects": ["$$this", {"status": status}]}, "$$this"]}}}}},
        {"$set": {"checklist_progress": {
            "booked": {"$size": {"$filter": {"input": "$bookings", "cond": {"$eq": ["$$this.status", "user_marked_booked"]}}}},
            "total": {"$size": "$bookings"}}}},
    ])
    return board_view(slug)


@app.get("/api/boards/{slug}/ics")
def board_ics(slug: str):
    v = board_view(slug)
    if not v:
        raise HTTPException(404, "Board not found.")
    b, links = v["board"], v["links"]

    def esc(t: str) -> str:
        return str(t).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Wander//Trip Board//EN", f"X-WR-CALNAME:{esc(b['title'])}"]
    for d in b["days"]:
        day = str(d["date"])[:10].replace("-", "")
        for sl in d["slots"]:
            it = sl.get("item") or {}
            hhmm = (sl.get("time") or "09:00").replace(":", "")[:4]
            urls = [links[str(lid)]["url"] for lid in it.get("link_ids", []) if str(lid) in links]
            desc = "\n".join([it.get("why", ""), *urls]).strip()
            lines += ["BEGIN:VEVENT", f"UID:{b['_id']}-{sl.get('slot_id', hhmm)}-{day}@wander", f"DTSTAMP:{_now():%Y%m%dT%H%M%SZ}",
                      f"DTSTART:{day}T{hhmm}00", "DURATION:PT1H30M", f"SUMMARY:{esc(it.get('name', 'Plan'))}",
                      f"DESCRIPTION:{esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return PlainTextResponse("\r\n".join(lines) + "\r\n", media_type="text/calendar",
                             headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'})


# ---------------------------------------------------------------- memory

@app.get("/api/memory")
def get_memory(user=User):
    return jsonable(memory.list_all(user["_id"]))


class MemoryIn(BaseModel):
    text: str


@app.post("/api/memory")
def add_memory(body: MemoryIn, user=User):
    if not body.text.strip():
        raise HTTPException(400, "Write something to remember.")
    return jsonable(memory.add(user["_id"], body.text, kind="episode", source="manual"))


@app.delete("/api/memory/{memory_id}")
def forget_memory(memory_id: str, user=User):
    if not memory.forget(user["_id"], memory_id):
        raise HTTPException(404, "Memory not found.")
    return {"ok": True}


# ---------------------------------------------------------------- autocomplete

@app.get("/api/autocomplete")
def autocomplete(q: str = ""):
    q = q.strip()
    if len(q) < 2:
        return []
    proj = {"_id": 0, "name": 1, "country": 1}
    try:
        hits = list(db().cities.aggregate([
            {"$search": {"index": "cities_autocomplete", "compound": {
                "must": [{"autocomplete": {"query": q, "path": "name"}}],
                "should": [{"near": {"path": "population", "origin": 40_000_000, "pivot": 2_000_000}}]}}},
            {"$limit": 8}, {"$project": proj}]))
        if hits:
            return hits
    except Exception as e:  # noqa: BLE001 - index not built yet
        log.info("autocomplete fallback: %s", type(e).__name__)
    return list(db().cities.find({"name": {"$regex": f"^{re.escape(q)}", "$options": "i"}}, proj)
                .sort("population", -1).limit(8))


# ---------------------------------------------------------------- voice (ElevenLabs client tools)

@app.post("/api/voice/session")
def voice_session(user=User):
    if not (config.ELEVENLABS_API_KEY and config.ELEVENLABS_AGENT_ID):
        raise HTTPException(503, "Voice isn't configured (ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID).")
    r = httpx.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                  params={"agent_id": config.ELEVENLABS_AGENT_ID},
                  headers={"xi-api-key": config.ELEVENLABS_API_KEY}, timeout=15)
    if r.status_code != 200:
        log.warning("elevenlabs signed url %s: %s", r.status_code, r.text[:200])
        raise HTTPException(502, "Voice service unavailable right now.")
    return {"signed_url": r.json()["signed_url"]}


@app.get("/api/slots")
def slot_schema():
    """Question templates (for docs/debugging)."""
    return {sid: {"impact": v[0], "required": v[1], "type": v[2], "prompt": v[3]} for sid, v in slots.SLOTS.items()}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Try again in a moment."}, status_code=500)
