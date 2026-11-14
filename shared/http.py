"""Mongo-cached outbound HTTP with bounded retries and secret-free cache keys."""

import hashlib
import ipaddress
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx


def now():
    return datetime.now(UTC)


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
        cached = self.db.search_cache.find_one({"_id": key, "expires_at": {"$gt": now()}})
        if cached:
            return cached["response"]
        target = public_url(url) if public else url
        with httpx.Client(timeout=12, headers={"User-Agent": "WanderBot/0.1"}) as client:
            for attempt in range(3):
                try:
                    response = client.request(
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
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                if attempt < 2:
                    time.sleep(0.25 * 2**attempt)
            if public and response.is_redirect:
                # Every redirect target must pass the same network boundary check.
                for _ in range(4):
                    target = public_url(str(response.url.join(response.headers["location"])))
                    response = client.request(method, target, follow_redirects=False)
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
                    import base64

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
