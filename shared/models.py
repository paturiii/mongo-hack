"""Track 1's frozen MongoDB document contracts (§6), including source timestamps."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Document(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    id: str = Field(alias="_id")


class Evidence(BaseModel):
    source_url: HttpUrl
    source_type: str
    summary: str
    fetched_at: datetime


class Action(BaseModel):
    type: Literal["link", "replan", "notice"]
    url: HttpUrl | None = None
    instruction: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class Option(BaseModel):
    id: str
    label: str
    actions: list[Action] = Field(min_length=1)


class Watch(Document):
    trip_id: str
    kind: Literal["flight", "train", "weather", "area", "venue", "event", "link", "price"]
    target: dict[str, Any]
    next_check_at: datetime
    interval_s: int = Field(ge=1)
    active: bool


class Signal(BaseModel):
    ts: datetime
    meta: dict[str, str]
    payload: dict[str, Any]
    hash: str


class Disruption(Document):
    trip_id: str
    kind: Literal[
        "flight_delay",
        "flight_cancel",
        "weather",
        "strike",
        "closure",
        "event_cancel",
        "dead_link",
        "price_spike",
    ]
    severity: int = Field(ge=1, le=5)
    detected_at: datetime
    evidence: list[Evidence] = Field(min_length=1)
    affected: dict[str, list[str]]
    options: list[Option] = Field(min_length=2, max_length=3)
    status: Literal["open", "notified", "resolved", "dismissed"]


class Alert(Document):
    user_id: str
    trip_id: str
    disruption_id: str
    channel: Literal["board", "email", "voice"]
    title: str
    body: str
    status: Literal["pending", "sent", "seen"]
    created_at: datetime


class RefineRequest(Document):
    trip_id: str
    source: Literal["user", "disruption"]
    disruption_id: str | None = None
    instruction: str
    scope: dict[str, list[str]]
    constraints: dict[str, Any]
    status: Literal["queued", "claimed", "done", "failed"]
    created_at: datetime
