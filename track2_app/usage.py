"""Per-user daily LLM call metering (APP-10)."""
from datetime import UTC, datetime

from fastapi import HTTPException
from pymongo import ReturnDocument

from shared import config
from shared.db import db

UPSELL = ("You've used today's free planning allowance. Wander Pro removes the limit; "
          "your trips and boards stay available in the meantime.")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def used(user_id: str) -> int:
    doc = db().usage.find_one({"user_id": user_id, "day": _today()})
    return doc["llm_calls"] if doc else 0


def check(user_id: str) -> None:
    if used(user_id) >= config.DAILY_LLM_QUOTA:
        raise HTTPException(429, UPSELL)


def charge(user_id: str, calls: int = 1) -> int:
    doc = db().usage.find_one_and_update({"user_id": user_id, "day": _today()}, {"$inc": {"llm_calls": calls}},
                                         upsert=True, return_document=ReturnDocument.AFTER)
    return doc["llm_calls"]
