"""All external HTTP goes through here: timeouts, retries, polite UA, Mongo TTL cache (§5.1 rule 7)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

USER_AGENT = "WanderBot/0.1 (+hackathon travel planner)"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}

_local = threading.local()


def client() -> httpx.Client:
    c = getattr(_local, "client", None)
    if c is None:
        c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        _local.client = c
    return c


def request(method: str, url: str, *, retries: int = 3, backoff: float = 1.5, **kw) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = client().request(method, url, **kw)
            if r.status_code in RETRY_STATUSES and attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
                continue
            return r
        except httpx.HTTPError as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    raise last or RuntimeError(f"request failed: {url}")


def get_json(url: str, **kw) -> Any:
    r = request("GET", url, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url: str, **kw) -> Any:
    r = request("POST", url, **kw)
    r.raise_for_status()
    return r.json()


def cache_key(kind: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return f"{kind}:{hashlib.sha256(raw.encode()).hexdigest()[:40]}"


def cached(kind: str, payload: Any, fn: Callable[[], Any], ttl: timedelta = timedelta(days=7)) -> Any:
    """Return search_cache[key].response or compute + store. Empty/None results are not cached."""
    from shared.config import get_db

    col = get_db().search_cache
    key = cache_key(kind, payload)
    now = datetime.now(timezone.utc)
    hit = col.find_one({"_id": key, "expires_at": {"$gt": now}})
    if hit is not None:
        return hit["response"]
    response = fn()
    if response:
        col.update_one(
            {"_id": key},
            {"$set": {"kind": kind, "query": payload, "response": response, "fetched_at": now, "expires_at": now + ttl}},
            upsert=True,
        )
    return response


def cache_lookup(kind: str, payload: Any) -> Any | None:
    from shared.config import get_db

    hit = get_db().search_cache.find_one({"_id": cache_key(kind, payload)})
    return hit["response"] if hit else None
