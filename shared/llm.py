"""Structured-output LLM calls (§5.1 rule 4). Model names come only from env.

OpenRouter reserves credit for max_tokens on every in-flight request, so completions are capped per tier and
concurrency is bounded per process (LLM_MAX_CONCURRENCY); 402 "in-flight" rejections are retried after a pause.

Call styles: structured(schema, system, user, tier="fast"|"smart")  (Track 3)
             structured(schema, system, user, smart=True)            (Track 2)
"""
import logging
import os
import threading
import time
from functools import lru_cache
from typing import Literal

from openai import APIStatusError, OpenAI
from pydantic import BaseModel

from shared.config import settings

log = logging.getLogger("wander.llm")

MAX_TOKENS = {"fast": int(os.environ.get("LLM_MAX_TOKENS_FAST", "3000")),
              "smart": int(os.environ.get("LLM_MAX_TOKENS_SMART", "6000"))}
_sem = threading.BoundedSemaphore(int(os.environ.get("LLM_MAX_CONCURRENCY", "4")))


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=120, max_retries=3)


def model_for(tier: Literal["fast", "smart"]) -> str:
    return settings.llm_model_fast if tier == "fast" else settings.llm_model_smart


def structured[T: BaseModel](schema: type[T], system: str, user: str, smart: bool = False, *,
                             tier: Literal["fast", "smart"] | None = None, max_tokens: int | None = None) -> T:
    tier = tier or ("smart" if smart else "fast")
    for attempt in range(6):
        try:
            with _sem:
                resp = _client().chat.completions.parse(
                    model=model_for(tier),
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format=schema,
                    max_tokens=max_tokens or MAX_TOKENS[tier],
                    extra_body={"provider": {"require_parameters": True}},
                )
            break
        except APIStatusError as exc:
            if exc.status_code != 402 or attempt == 5:
                raise
            log.warning("LLM 402 (credits reserved by in-flight requests); retry %d", attempt + 1)
            time.sleep(4 * (attempt + 1))
    msg = resp.choices[0].message
    if msg.parsed is None:
        return schema.model_validate_json(msg.content or "{}")
    return msg.parsed


def text(system: str, user: str, smart: bool = True, max_tokens: int = 1200) -> str:
    """Short free-text reply. Reasoning models spend part of max_tokens thinking, so keep headroom + low effort."""
    with _sem:
        resp = _client().chat.completions.create(
            model=model_for("smart" if smart else "fast"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            extra_body={"reasoning": {"effort": "low"}},
        )
    out = (resp.choices[0].message.content or "").strip()
    if not out:
        raise RuntimeError("LLM returned empty text")
    return out
