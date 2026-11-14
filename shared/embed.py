"""Voyage embeddings + rerank."""
from functools import lru_cache

import voyageai

from shared import config


@lru_cache
def _client() -> voyageai.Client:
    return voyageai.Client(api_key=config.VOYAGE_API_KEY)


def embed(texts: list[str], query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    r = _client().embed(texts, model=config.VOYAGE_EMBED_MODEL, input_type="query" if query else "document")
    return r.embeddings


def rerank(query: str, docs: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    r = _client().rerank(query, docs, model=config.VOYAGE_RERANK_MODEL, top_k=top_k)
    return [(x.index, x.relevance_score) for x in r.results]
