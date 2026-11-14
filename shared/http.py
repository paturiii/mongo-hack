"""All external HTTP: timeouts, retries, polite UA, Mongo TTL cache (§5.1 rule 7).

Two layers, used by different tracks:
- module functions request/get_json/post_json/cached (Track 3)
- CachedHTTP(db) with public-URL guard + secret-free cache keys (Track 1), and now()
"""
import base64
import hashlib
import ipaddress
import json
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

USER_AGENT = "WanderBot/0.1 (+hackathon travel planner)"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}

_local = threading.local()


def now():
    return datetime.now(UTC)


# ---------------------------------------------------------------- Track 3: module functions

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
    t = now()
    hit = col.find_one({"_id": key, "expires_at": {"$gt": t}})
    if hit is not None:
        return hit["response"]
    response = fn()
    if response:
        col.update_one(
            {"_id": key},
            {"$set": {"kind": kind, "query": payload, "response": response, "fetched_at": t, "expires_at": t + ttl}},
            upsert=True,
        )
    return response


def cache_lookup(kind: str, payload: Any) -> Any | None:
    from shared.config import get_db

    hit = get_db().search_cache.find_one({"_id": cache_key(kind, payload)})
    return hit["response"] if hit else None


# ---------------------------------------------------------------- Track 1: CachedHTTP

def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
        raise ValueError("Expected a public HTTP(S) URL")
    for entry in socket.getaddrinfo(parts.hostname, parts.port or 443):
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise ValueError("Private network URLs cannot be checked")
    return url


class CachedHTTP:
    def __init__(self, db):
        self.db = db

    def request(
        self, method, url, *, headers=None, params=None, json_body=None, ttl=3600, public=False
    ):
        # Hash parameters (which may contain provider keys); never persist headers or raw URL.
        idempotency = (headers or {}).get("Idempotency-Key")
        fingerprint = json.dumps(
            [method, url, params, json_body, idempotency], sort_keys=True, default=str
        )
        key = hashlib.sha256(fingerprint.encode()).hexdigest()
        hit = self.db.search_cache.find_one({"_id": key, "expires_at": {"$gt": now()}})
        if hit:
            return hit["response"]
        target = public_url(url) if public else url
        with httpx.Client(timeout=12, headers={"User-Agent": "WanderBot/0.1"}) as http_client:
            for attempt in range(3):
                try:
                    response = http_client.request(
                        method,
                        target,
                        headers=headers,
                        params=params,
                        json=json_body,
                        follow_redirects=False,
                    )
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    time.sleep(0.25 * 2**attempt)
                    continue
                if response.status_code not in RETRY_STATUSES:
                    break
                if attempt < 2:
                    time.sleep(0.25 * 2**attempt)
            if public and response.is_redirect:
                # Every redirect target must pass the same network boundary check.
                for _ in range(4):
                    target = public_url(str(response.url.join(response.headers["location"])))
                    response = http_client.request(method, target, follow_redirects=False)
                    if not response.is_redirect:
                        break
            if public:
                result = {
                    "status_code": response.status_code,
                    "url": str(response.url),
                    "ok": 200 <= response.status_code < 400 and not response.is_redirect,
                }
            else:
                response.raise_for_status()
                if "application/json" in response.headers.get("content-type", ""):
                    result = response.json()
                else:
                    result = {"base64": base64.b64encode(response.content).decode()}
        self.db.search_cache.update_one(
            {"_id": key},
            {
                "$set": {
                    "response": result,
                    "fetched_at": now(),
                    "expires_at": now() + timedelta(seconds=ttl),
                }
            },
            upsert=True,
        )
        return result
