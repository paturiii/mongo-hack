"""Read models the web app renders. Pure Mongo reads, no LLM."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from bson import ObjectId

from shared.db import db


def _ids(values) -> list:
    """Other tracks may key docs by string or ObjectId; match both."""
    out = []
    for v in values:
        if v is None:
            continue
        out.append(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            out.append(ObjectId(v))
        elif isinstance(v, ObjectId):
            out.append(str(v))
    return out


def jsonable(x):
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items() if k != "embedding"}
    if isinstance(x, list):
        return [jsonable(v) for v in x]
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if type(x).__name__ == "ObjectId":
        return str(x)
    return x


def progress(trip: dict) -> dict:
    jobs = list(db().research_jobs.find({"trip_id": trip["_id"]}, {"kind": 1, "status": 1}))
    by_kind: dict = {}
    for j in jobs:
        k = by_kind.setdefault(j["kind"], {"done": 0, "total": 0})
        k["total"] += 1
        k["done"] += j["status"] in ("done", "failed")
    city = (trip.get("destination") or {}).get("name")
    opinions = db().opinions.count_documents({"city": city}) if city else 0
    sources = sorted({o for o in db().opinions.distinct("source_name", {"city": city})}) if city else []
    return {"jobs_total": len(jobs), "jobs_done": sum(v["done"] for v in by_kind.values()),
            "by_kind": by_kind, "opinions": opinions, "sources": sources[:6]}


def board_summary(trip_id: str) -> dict | None:
    b = db().boards.find_one({"trip_id": trip_id}, {"share_slug": 1, "title": 1, "version": 1}, sort=[("version", -1)])
    return {"slug": b["share_slug"], "title": b.get("title"), "version": b.get("version")} if b else None


def session_view(session_id: str, user_id: str) -> dict | None:
    s = db().sessions.find_one({"_id": session_id, "user_id": user_id})
    if not s:
        return None
    trip = db().trips.find_one({"_id": s.get("trip_id")}) or {}
    q = db().questions.find_one({"session_id": session_id, "status": "open"}, sort=[("batch", -1)])
    board = board_summary(trip["_id"]) if trip else None
    status = s["status"]
    if board and status in ("researching", "planning"):
        status = "ready"
        db().sessions.update_one({"_id": session_id}, {"$set": {"status": "ready"}})
    return jsonable({
        "session": {"id": s["_id"], "status": status, "messages": s.get("messages", []), "created_at": s.get("created_at")},
        "trip": {k: trip.get(k) for k in ("_id", "destination", "origin", "dates", "travelers", "legs", "profile", "status")},
        "questions": q,
        "progress": progress(trip) if trip else None,
        "board": board,
    })


_pool = ThreadPoolExecutor(max_workers=16)
ALERT_FIELDS = {k: 1 for k in ("_id", "disruption_id", "channel", "title", "body", "audio_url", "status",
                               "delivery_mode", "last_error", "created_at", "source_url", "source_type", "fetched_at")}


def board_view(slug: str) -> dict | None:
    b = db().boards.find_one({"share_slug": slug}, sort=[("version", -1)])
    if not b:
        return None
    items = [sl.get("item") or {} for d in b.get("days", []) for sl in d.get("slots", [])]
    place_ids = {it.get("place_id") for it in items}
    link_ids = {lid for it in items for lid in it.get("link_ids", [])} | {bk["link_id"] for bk in b.get("bookings", [])}
    tid = b["trip_id"]
    # Independent lookups run concurrently: one round-trip of latency instead of five (this feeds the live board).
    f_geo = _pool.submit(lambda: list(db().places.find({"_id": {"$in": _ids(place_ids)}}, {"geo": 1})))
    f_links = _pool.submit(lambda: list(db().links.find({"_id": {"$in": _ids(link_ids)}})))
    # Superset of Track 1's snapshot: every channel's alert (voice audio, email status) + recent disruption history.
    f_alerts = _pool.submit(lambda: list(db().alerts.find({"trip_id": tid}, ALERT_FIELDS).sort("created_at", -1).limit(50)))
    f_dis = _pool.submit(lambda: list(db().disruptions.find({"trip_id": tid}).sort("detected_at", -1).limit(50)))
    f_trip = _pool.submit(lambda: db().trips.find_one({"_id": tid}, {"destination": 1, "dates": 1, "travelers": 1, "user_id": 1}))
    geo = {str(p["_id"]): p.get("geo") for p in f_geo.result()}
    for it in items:
        if not it.get("geo") and str(it.get("place_id")) in geo:
            it["geo"] = geo[str(it["place_id"])]
    links = {str(lk["_id"]): lk for lk in f_links.result()}
    return jsonable({"board": b, "links": links, "alerts": f_alerts.result(), "disruptions": f_dis.result(),
                     "trip": f_trip.result()})
