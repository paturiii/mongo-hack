"""LLM access (OpenAI-compatible, OpenRouter by default). Models come only from env (MASTERPLAN §5.1)."""
from functools import lru_cache

from openai import OpenAI
from pydantic import BaseModel

from shared import config


@lru_cache
def _client() -> OpenAI:
    return OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


def structured[T: BaseModel](schema: type[T], system: str, user: str, smart: bool = False, max_tokens: int = 4000) -> T:
    """One structured-output call. Returns a validated Pydantic object."""
    model = config.LLM_MODEL_SMART if smart else config.LLM_MODEL_FAST
    resp = _client().chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=schema,
        max_tokens=max_tokens,  # without a cap OpenRouter reserves the full 65k context per call
        extra_body={"provider": {"require_parameters": True}},
    )
    parsed = resp.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("LLM returned no structured output")
    return parsed


def text(system: str, user: str, smart: bool = True, max_tokens: int = 1200) -> str:
    """Short free-text reply. Reasoning models spend part of max_tokens thinking, so keep headroom + low effort."""
    model = config.LLM_MODEL_SMART if smart else config.LLM_MODEL_FAST
    resp = _client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        extra_body={"reasoning": {"effort": "low"}},
    )
    out = (resp.choices[0].message.content or "").strip()
    if not out:
        raise RuntimeError("LLM returned empty text")
    return out
