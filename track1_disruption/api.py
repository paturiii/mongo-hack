"""Track 1 local gateway; Track 2 may mount this router in its gateway unchanged."""

import asyncio
import hashlib
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta

from bson import ObjectId
from fastapi import APIRouter, Cookie, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from shared.config import ROOT, Settings
from shared.http import now
from track1_disruption.fixture_consumer import consume
from track1_disruption.graph import resume_choice
from track1_disruption.resolve import ResolutionConflict, dismiss, resolve

router = APIRouter()
log = logging.getLogger("wander.disruption.api")


def serial(doc):
    return jsonable_encoder(doc, custom_encoder={ObjectId: str})


def identity(request, token):
    if not token:
        raise HTTPException(401, "Sign in to choose a fix")
    session = request.app.state.db.auth_sessions.find_one(
        {
            "_id": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": {"$gt": now()},
        }
    )
    if not session:
        raise HTTPException(401, "Session expired; sign in again")
    return session["user_id"]


def same_origin(request):
    origin = request.headers.get("origin")
    expected = {
        request.app.state.settings.get("WEB_BASE_URL", "http://localhost:3000"),
        "http://127.0.0.1:3000",
        str(request.base_url).rstrip("/"),
    }
    if origin and origin not in expected:
        raise HTTPException(403, "Request origin not allowed")


@router.get("/health")
def health(request: Request):
    request.app.state.db.command("ping")
    return {"status": "ok", "track": 1}


@router.post("/api/demo/session")
def demo_session(request: Request, response: Response):
    same_origin(request)
    if request.app.state.settings.mode != "fixtures" or request.client.host not in {
        "127.0.0.1",
        "::1",
        "testclient",
    }:
        raise HTTPException(404)
    if not request.app.state.db.users.find_one({"_id": "user_lisbon_fixture", "fixture": True}):
        raise HTTPException(409, "Load fixtures first")
    token = secrets.token_urlsafe(32)
    request.app.state.db.auth_sessions.insert_one(
        {
            "_id": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": "user_lisbon_fixture",
            "created_at": now(),
            "expires_at": now() + timedelta(hours=8),
        }
    )
    response.set_cookie("wander_session", token, httponly=True, samesite="strict", max_age=8 * 3600)
    return {"user_id": "user_lisbon_fixture", "fixture": True}


def snapshot(db, slug):
    board = db.boards.find_one({"share_slug": slug})
    if not board:
        raise HTTPException(404, "Board not found")
    disruptions = list(
        db.disruptions.find({"trip_id": board["trip_id"]}).sort("detected_at", -1).limit(50)
    )
    alerts = list(
        db.alerts.find(
            {"trip_id": board["trip_id"]},
            {
                "_id": 1,
                "disruption_id": 1,
                "channel": 1,
                "title": 1,
                "body": 1,
                "audio_url": 1,
                "status": 1,
                "delivery_mode": 1,
                "last_error": 1,
                "created_at": 1,
                "source_url": 1,
                "source_type": 1,
                "fetched_at": 1,
            },
        )
    )
    return serial({"board": board, "disruptions": disruptions, "alerts": alerts})


@router.get("/api/boards/{slug}")
def get_board(slug: str, request: Request):
    return snapshot(request.app.state.db, slug)


@router.get("/api/trips/{trip_id}/disruptions")
def get_disruptions(trip_id: str, request: Request, wander_session: str | None = Cookie(None)):
    user_id = identity(request, wander_session)
    if not request.app.state.db.trips.find_one({"_id": trip_id, "user_id": user_id}):
        raise HTTPException(404)
    return serial(list(request.app.state.db.disruptions.find({"trip_id": trip_id})))


@router.get("/api/trips/{trip_id}/board")
def trip_board(trip_id: str, request: Request, wander_session: str | None = Cookie(None)):
    user_id = identity(request, wander_session)
    if not request.app.state.db.trips.find_one({"_id": trip_id, "user_id": user_id}):
        raise HTTPException(404)
    board = request.app.state.db.boards.find_one({"trip_id": trip_id}, {"share_slug": 1})
    if not board:
        raise HTTPException(404)
    return {"share_slug": board["share_slug"]}


@router.get("/api/boards/{slug}/events")
async def events(slug: str, request: Request):
    initial = await run_in_threadpool(snapshot, request.app.state.db, slug)

    async def generate():
        previous = json.dumps(initial, sort_keys=True)
        yield "event: board\ndata: " + previous + "\n\n"
        # MongoDB change stream, scoped to this board/trip; snapshot sweep recovers disconnects.
        trip_id = initial["board"]["trip_id"]
        pipeline = [
            {
                "$match": {
                    "$or": [
                        {"ns.coll": "boards", "documentKey._id": initial["board"]["_id"]},
                        {
                            "ns.coll": {"$in": ["alerts", "disruptions"]},
                            "fullDocument.trip_id": trip_id,
                        },
                    ]
                }
            }
        ]
        stream = None
        try:
            stream = await run_in_threadpool(
                lambda: request.app.state.db.watch(
                    pipeline, full_document="updateLookup", max_await_time_ms=500
                )
            )
            last_sweep = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                changed = await run_in_threadpool(stream.try_next)
                current_time = asyncio.get_running_loop().time()
                if changed or current_time - last_sweep >= 1:
                    data = await run_in_threadpool(snapshot, request.app.state.db, slug)
                    encoded = json.dumps(data, sort_keys=True)
                    if encoded != previous:
                        yield "event: board\ndata: " + encoded + "\n\n"
                        previous = encoded
                    last_sweep = current_time
                if not changed:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.1)
        finally:
            if stream is not None:
                await run_in_threadpool(stream.close)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class Choice(BaseModel):
    option_id: str = Field(min_length=1, max_length=100)


@router.post("/api/disruptions/{disruption_id}/resolve")
def choose(
    disruption_id: str, choice: Choice, request: Request, wander_session: str | None = Cookie(None)
):
    same_origin(request)
    user_id = identity(request, wander_session)
    db, settings = request.app.state.db, request.app.state.settings
    try:
        result = resolve(db, disruption_id, choice.option_id, user_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except KeyError:
        raise HTTPException(404, "Disruption not found") from None
    except ResolutionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    # Mongo is the authoritative handoff; checkpoint recovery cannot undo a committed choice.
    try:
        resume_choice(db, settings, disruption_id, choice.option_id)
        if settings.mode == "fixtures" and result["refine_request_id"]:
            result["fixture_board_version"] = consume(db, settings, result["refine_request_id"])
    except Exception as exc:
        log.warning("Post-resolution work deferred (%s)", type(exc).__name__)
        result["handoff_status"] = "queued"
    return result


@router.post("/api/disruptions/{disruption_id}/dismiss")
def dismiss_disruption(
    disruption_id: str, request: Request, wander_session: str | None = Cookie(None)
):
    same_origin(request)
    try:
        return dismiss(request.app.state.db, disruption_id, identity(request, wander_session))
    except PermissionError:
        raise HTTPException(403, "Disruption not available") from None
    except ResolutionConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/media/voice/{filename}")
def audio(filename: str):
    if "/" in filename or "\\" in filename or not filename.endswith(".mp3"):
        raise HTTPException(404)
    target = ROOT / "media" / "voice" / filename
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, media_type="audio/mpeg")


@asynccontextmanager
async def lifespan(app):
    settings = Settings.load()
    client, db = await run_in_threadpool(settings.connect)
    app.state.settings, app.state.db = settings, db
    yield
    client.close()


app = FastAPI(title="Wander Disruption API", lifespan=lifespan)
app.include_router(router)
