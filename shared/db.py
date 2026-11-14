"""Shared Mongo handles. Sync client for request handlers + graphs, async client for change streams."""
from functools import lru_cache

from pymongo import AsyncMongoClient, MongoClient

from shared import config


@lru_cache
def client() -> MongoClient:
    config.require_mongo()
    return MongoClient(config.MONGODB_URI, tz_aware=True)


def db():
    return client()[config.MONGODB_DB]


@lru_cache
def aclient() -> AsyncMongoClient:
    return AsyncMongoClient(config.MONGODB_URI, tz_aware=True)


def adb():
    return aclient()[config.MONGODB_DB]
