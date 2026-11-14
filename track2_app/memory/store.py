"""Long-term traveler memory (APP-08). `memories` is the store: prefs (key/value), episodes and feedback (text).

Text memories get Voyage embeddings for recall via Atlas Vector Search (`memories_vector`). Embedding is
best-effort: if Voyage is throttled the memory is still saved and recall falls back to recency.
"""
import logging
import math
import secrets
from datetime import UTC, datetime

from bson import ObjectId

from shared.db import db

log = logging.getLogger("wander.memory")

# Slots worth remembering across trips (trip-specific ones like dates are not).
PREF_KEYS = {"budget_band", "pace", "stay_vibe", "nightlife_intensity", "dietary", "mobility", "stay_type",
             "booking_ahead", "transport_pref", "dealbreakers", "origin"}
LABELS = {"budget_band": "budget", "pace": "pace", "stay_vibe": "stay vibe", "nightlife_intensity": "nights out",
          "dietary": "diet", "mobility": "mobility", "stay_type": "stay type", "booking_ahead": "booking style",
          "transport_pref": "transport", "dealbreakers": "dealbreakers", "origin": "home city"}


def _now() -> datetime:
    return datetime.now(UTC)


def _embed(text: str, query: bool = False) -> list[float] | None:
    try:
        from shared.embed import embed
        return embed([text], query=query, wait=False)[0]
    except Exception as e:  # noqa: BLE001 - Voyage throttling must not break intake
        log.warning("embedding skipped: %s", type(e).__name__)
        return None


def prefs(user_id: str) -> dict:
    """{key: value} for every remembered preference."""
    return {m["key"]: m["value"] for m in db().memories.find({"user_id": user_id, "kind": "pref"})}


def remember_prefs(user_id: str, answers: dict, source: str = "answer") -> list[str]:
    saved = []
    for key, value in answers.items():
        if key not in PREF_KEYS or value in (None, "", []):
            continue
        db().memories.update_one(
            {"user_id": user_id, "kind": "pref", "key": key},
            {"$set": {"value": value, "text": f"{LABELS.get(key, key)}: {value}", "confidence": 0.9,
                      "source": source, "updated_at": _now()},
             "$setOnInsert": {"_id": f"mem_{secrets.token_hex(6)}"}},
            upsert=True)
        saved.append(key)
    return saved


def add(user_id: str, text: str, kind: str = "episode", source: str = "manual", key: str | None = None) -> dict:
    doc = {"_id": f"mem_{secrets.token_hex(6)}", "user_id": user_id, "kind": kind, "key": key, "value": None,
           "text": text.strip(), "confidence": 0.8, "source": source, "updated_at": _now()}
    vec = _embed(doc["text"])
    if vec:
        doc["embedding"] = vec
    db().memories.insert_one(doc)
    return doc


def recall(user_id: str, query: str, k: int = 3) -> list[dict]:
    """Episodes/feedback most relevant to `query`: Atlas Vector Search → local cosine → most recent."""
    filt = {"user_id": user_id, "kind": {"$in": ["episode", "feedback"]}}
    qvec = _embed(query, query=True) if query else None
    if qvec:
        try:
            hits = list(db().memories.aggregate([
                {"$vectorSearch": {"index": "memories_vector", "path": "embedding", "queryVector": qvec,
                                   "numCandidates": 50, "limit": k,
                                   "filter": {"user_id": user_id, "kind": {"$in": ["episode", "feedback"]}}}},
                {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
            ]))
            if hits:
                return hits
        except Exception as e:  # noqa: BLE001 - index still building / unavailable
            log.info("vector recall fallback: %s", type(e).__name__)
        docs = list(db().memories.find({**filt, "embedding": {"$exists": True}}))
        if docs:
            def cos(a, b):
                dot = sum(x * y for x, y in zip(a, b))
                return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)
            docs.sort(key=lambda m: cos(qvec, m["embedding"]), reverse=True)
            return [{**{k2: v for k2, v in m.items() if k2 != "embedding"}} for m in docs[:k]]
    return list(db().memories.find(filt, {"embedding": 0}).sort("updated_at", -1).limit(k))


def list_all(user_id: str) -> list[dict]:
    return list(db().memories.find({"user_id": user_id}, {"embedding": 0}).sort([("kind", 1), ("updated_at", -1)]))


def forget(user_id: str, memory_id: str) -> bool:
    q = {"user_id": user_id, "_id": memory_id}
    if ObjectId.is_valid(memory_id):
        q = {"user_id": user_id, "_id": {"$in": [memory_id, ObjectId(memory_id)]}}
    return db().memories.delete_one(q).deleted_count == 1


def usual_note(prefilled: dict) -> str | None:
    """'using your usual: relaxed, mid-range' for the assistant message."""
    order = ["pace", "budget_band", "stay_vibe", "nightlife_intensity", "dietary", "stay_type", "dealbreakers"]
    keys = [k for k in order if k in prefilled]
    if not keys:
        return None
    nice = {"mid": "mid-range", "big_night_out": "a big night out", "every_night": "out every night",
            "historic": "historic area", "lively": "lively area", "quiet": "quiet area", "beach": "near the beach"}

    def fmt(v):
        return ", ".join(map(str, v)) if isinstance(v, list) else nice.get(str(v), str(v).replace("_", " "))
    return "using your usual: " + ", ".join(fmt(prefilled[k]) for k in keys)
