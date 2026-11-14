"""R-12 Scheduling: pace-based slot times, arrival-day buffer from trips.legs, day-trip structure, nightlife timing."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEPLANE_MIN = 45
CHECKIN_MIN = 60


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _round_up(dt: datetime, minutes: int = 30) -> datetime:
    extra = (-dt.minute) % minutes
    return (dt + timedelta(minutes=extra)).replace(second=0, microsecond=0)


def _parse_dt(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=ZoneInfo("UTC"))
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T", 1))
        return d if d.tzinfo else d.replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        return None


def arrival_info(legs: list[dict], dest_iata: str | None, tz: str, transfer_min: int, per_day: int) -> dict:
    """When the traveler is free on day 1, and how many activities fit."""
    leg = next((l for l in legs if dest_iata and str(l.get("to", "")).upper() == dest_iata.upper()), legs[0] if legs else None)
    zone = ZoneInfo(tz or "UTC")
    arr = _parse_dt(leg.get("arr_at")) if leg else None
    if not arr:
        return {"leg": leg, "arr_local": None, "free_from": "13:00", "capacity": max(1, per_day - 1)}
    arr_local = arr.astimezone(zone)
    free = _round_up(arr_local + timedelta(minutes=DEPLANE_MIN + transfer_min + CHECKIN_MIN))
    hours_left = 19.5 - (free.hour + free.minute / 60)
    capacity = 0 if hours_left < 2 else (1 if hours_left < 5 else min(per_day, 2 if hours_left < 7.5 else per_day))
    return {"leg": leg, "arr_local": arr_local, "transfer_at": _hm(arr_local + timedelta(minutes=DEPLANE_MIN)),
            "checkin_at": _hm(arr_local + timedelta(minutes=DEPLANE_MIN + transfer_min)), "free_from": _hm(free), "capacity": capacity}


def _add(t: str, minutes: int) -> str:
    h, m = map(int, t.split(":"))
    total = h * 60 + m + minutes
    return f"{min(23, total // 60):02d}:{total % 60:02d}"


def schedule_day(i: int, dp: dict, arrival: dict | None, per_day: int) -> list[dict]:
    """Returns [{time, kind, ...payload}] ordered by time. Payload keys: place | event | transit."""
    slots: list[dict] = []
    acts = dp["activities"]
    if i == 0 and arrival and arrival.get("arr_local"):
        slots.append({"time": _hm(arrival["arr_local"]), "kind": "arrival"})
        slots.append({"time": arrival["transfer_at"], "kind": "transfer"})
        slots.append({"time": arrival["checkin_at"], "kind": "checkin"})
        t = arrival["free_from"]
        for p in acts:
            slots.append({"time": t, "kind": "activity", "place": p})
            t = _add(t, 120)
    elif dp["kind"] == "day_trip":
        slots.append({"time": "08:45", "kind": "day_trip_out"})
        times = ["10:00", "14:00", "15:45", "17:00"]
        for p, t in zip(acts, times):
            slots.append({"time": t, "kind": "activity", "place": p})
        slots.append({"time": "18:30", "kind": "day_trip_back"})
    else:
        times = {2: ["10:00", "15:00"], 3: ["10:00", "14:30", "16:30"], 4: ["09:30", "11:15", "14:30", "16:30"]}.get(per_day, ["10:00", "14:30", "16:30"])
        for p, t in zip(acts, times):
            slots.append({"time": t, "kind": "activity", "place": p})
    if dp.get("lunch"):
        slots.append({"time": "12:30" if dp["kind"] == "day_trip" else "13:00", "kind": "lunch", "place": dp["lunch"]})
    if dp.get("dinner"):
        slots.append({"time": "20:30" if dp["kind"] == "day_trip" else "20:00", "kind": "dinner", "place": dp["dinner"]})
    night = dp.get("night") or {}
    if night.get("event"):
        st = (night["event"].get("event") or {}).get("start_time") or "23:00"
        st = st if st >= "21:30" else "22:30"
        slots.append({"time": st, "kind": "night", "event": night["event"]})
    elif night.get("place"):
        slots.append({"time": "22:30", "kind": "night", "place": night["place"]})
    slots.sort(key=lambda s: s["time"])
    for j, s in enumerate(slots):
        s["slot_id"] = f"d{i + 1}s{j + 1}"
    return slots
