"""Voyage embeddings + reranker.

Calls are paced by a cross-process limiter stored in Mongo (`rate_limits` collection) so every worker, the planner
and scripts share one budget. Defaults match Voyage's no-payment-method limits (3 RPM / 10K TPM); raise them with
VOYAGE_RPM / VOYAGE_TPM once a payment method is on the account. Embeddings are cached in search_cache.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import voyageai
import voyageai.error

from shared.config import get_db, settings

log = logging.getLogger("wander.embed")

RPM = float(os.environ.get("VOYAGE_RPM", "3"))
TPM = float(os.environ.get("VOYAGE_TPM", "10000"))
MAX_TOKENS_PER_CALL = int(min(TPM * 0.9, 100_000))


@lru_cache(maxsize=1)
def _client() -> voyageai.Client:
    return voyageai.Client(api_key=settings.voyage_api_key, max_retries=0, timeout=90)


def _tokens(text: str) -> int:
    return max(1, len(text) // 3)


def _reserve(tokens: int) -> None:
    """Reserve a slot in the shared budget and sleep until it starts."""
    interval = max(60.0 / RPM, 60.0 * tokens / TPM) * 1.05
    now = datetime.now(timezone.utc)
    prev = get_db().rate_limits.find_one_and_update(
        {"_id": "voyage"},
        [{"$set": {"next_at": {"$add": [{"$max": [{"$ifNull": ["$next_at", now]}, now]}, int(interval * 1000)]}}}],
        upsert=True,
    )
    start = max(now, (prev or {}).get("next_at") or now)
    wait = (start - now).total_seconds()
    if wait > 0:
        log.info("voyage limiter: waiting %.0fs", wait)
        time.sleep(wait)


def _call(fn, tokens: int):
    for attempt in range(6):
        _reserve(tokens)
        try:
            return fn()
        except (voyageai.error.RateLimitError, voyageai.error.ServiceUnavailableError, voyageai.error.Timeout) as exc:
            log.warning("voyage %s, retry %d", type(exc).__name__, attempt + 1)
            time.sleep(min(60, 10 * (attempt + 1)))
    return fn()


def _ckey(text: str, input_type: str) -> str:
    return "emb:" + hashlib.sha256(f"{settings.voyage_embed_model}|{input_type}|{text}".encode()).hexdigest()[:40]


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    texts = [(t or " ")[:3000] for t in texts]
    col = get_db().search_cache
    keys = [_ckey(t, input_type) for t in texts]
    cached = {d["_id"]: d["response"] for d in col.find({"_id": {"$in": list(set(keys))}})}
    todo = [i for i, k in enumerate(keys) if k not in cached]
    batch: list[int] = []
    budget = 0

    def flush(idx: list[int], toks: int) -> None:
        if not idx:
            return
        res = _call(lambda: _client().embed([texts[i] for i in idx], model=settings.voyage_embed_model, input_type=input_type), toks)
        exp = datetime.now(timezone.utc) + timedelta(days=60)
        for i, v in zip(idx, res.embeddings):
            cached[keys[i]] = v
            col.update_one({"_id": keys[i]}, {"$set": {"kind": "embedding", "response": v, "expires_at": exp}}, upsert=True)

    for i in todo:
        t = _tokens(texts[i])
        if batch and (budget + t > MAX_TOKENS_PER_CALL or len(batch) >= 900):
            flush(batch, budget)
            batch, budget = [], 0
        batch.append(i)
        budget += t
    flush(batch, budget)
    return [cached[k] for k in keys]


def embed_query(text: str) -> list[float]:
    return embed([text], input_type="query")[0]


def rerank(query: str, documents: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
    """Returns [(index_into_documents, relevance)] best first."""
    if not documents:
        return []
    docs = [d[:600] for d in documents]
    toks = _tokens(query) * len(docs) + sum(_tokens(d) for d in docs)
    res = _call(lambda: _client().rerank(query, docs, model=settings.voyage_rerank_model, top_k=top_k), toks)
    return [(r.index, float(r.relevance_score)) for r in res.results]
