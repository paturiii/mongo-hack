"""Provider adapters normalize recorded/live responses into sourced observations."""

import json
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from shared.config import ROOT
from shared.http import CachedHTTP, now
from shared.search_client import news_search as search
from shared.structured import structured


class NewsFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevant: bool
    kind: str
    date: str
    summary: str
    source_url: str


def observation(kind, payload, url, source_type="provider"):
    return {
        "kind": kind,
        **payload,
        "evidence": [
            {
                "source_url": url,
                "source_type": source_type,
                "summary": payload.get("summary", kind.replace("_", " ")),
                "fetched_at": now(),
            }
        ],
    }


def recorded(kind, watch):
    payload = json.loads((ROOT / "fixtures" / "http" / f"{kind}.json").read_text())
    payload["date"] = watch["target"].get("date")
    payload.update(
        {
            k: v
            for k, v in watch["target"].items()
            if k in {"place_id", "event_id", "link_id", "area_geo", "number", "carrier"}
        }
    )
    payload["evidence"][0]["fetched_at"] = now()
    payload["fixture"] = True
    return payload


def poll(db, settings, watch):
    kind, target = watch["kind"], watch["target"]
    if settings.mode == "fixtures":
        return recorded(kind, watch)
    http = CachedHTTP(db)
    if kind in {"flight", "train"}:
        # User chose simulator flights: no flight API is required or silently invented.
        if not settings.get("FLIGHT_STATUS_API_KEY"):
            return None
        number = target.get("carrier", "") + target.get("number", "")
        provider = settings.get("FLIGHT_STATUS_PROVIDER")
        if provider == "aviationstack":
            data = http.request(
                "GET",
                "https://api.aviationstack.com/v1/flights",
                params={
                    "access_key": settings.get("FLIGHT_STATUS_API_KEY"),
                    "flight_iata": number,
                    "flight_date": target["date"],
                },
                ttl=300,
            )
            rows = data.get("data", [])
            if not rows:
                return None
            flight = rows[0]
            delay = (flight.get("arrival") or {}).get("delay") or 0
            cancelled = flight.get("flight_status") == "cancelled"
        elif provider == "aeroapi":
            data = http.request(
                "GET",
                "https://aeroapi.flightaware.com/aeroapi/flights/" + number,
                headers={"x-apikey": settings.get("FLIGHT_STATUS_API_KEY")},
                params={"start": target["date"]},
                ttl=300,
            )
            flight = next(
                (
                    row
                    for row in data.get("flights", [])
                    if str(row.get("scheduled_out", ""))[:10] == target["date"]
                ),
                None,
            )
            if not flight:
                return None
            delay, cancelled = (
                (flight.get("arrival_delay") or 0) / 60,
                flight.get("cancelled", False),
            )
        else:
            raise ValueError("Unsupported FLIGHT_STATUS_PROVIDER")
        if not cancelled and delay <= 120:
            return None
        return observation(
            "flight_cancel" if cancelled else "flight_delay",
            {
                **target,
                "minutes": int(delay),
                "gate": (flight.get("arrival") or {}).get("gate"),
                "summary": f"{number}: cancelled"
                if cancelled
                else f"{number}: {int(delay)} minute delay",
            },
            "https://www.flightaware.com/live/flight/" + number,
        )
    if kind == "weather":
        polygon = target["area_geo"]["coordinates"][0]
        lng, lat = polygon[0]
        date = target["date"]
        data = http.request(
            "GET",
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lng,
                "daily": "precipitation_probability_max,wind_speed_10m_max,apparent_temperature_max",
                "start_date": date,
                "end_date": date,
                "timezone": "auto",
            },
            ttl=3600,
        )
        daily = data.get("daily", {})
        rain = (daily.get("precipitation_probability_max") or [0])[0] or 0
        wind = (daily.get("wind_speed_10m_max") or [0])[0] or 0
        heat = (daily.get("apparent_temperature_max") or [0])[0] or 0
        if rain < 70 and wind < 50 and heat < 38:
            return None
        return observation(
            "weather",
            {
                **target,
                "rain_probability": rain,
                "wind_kmh": wind,
                "heat_index": heat,
                "summary": "Adverse outdoor weather forecast",
            },
            "https://open-meteo.com/",
            "weather",
        )
    if kind in {"area", "venue"}:
        place = db.places.find_one({"_id": target.get("place_id")}) or {}
        query = f"{place.get('name', target.get('city', ''))} strike protest closure renovation {target.get('date', '')}"
        findings = search(db, settings, query)
        if not findings:
            return None
        finding = structured(
            db,
            settings,
            NewsFinding,
            json.dumps(
                {
                    "target": target,
                    "sources": findings,
                    "task": "Classify closures/strikes only on the target date and location. Use exactly one supplied URL, summarize in your own words; irrelevant unless dates match.",
                },
                default=str,
            ),
        )
        urls = {row["url"] for row in findings}
        if (
            not finding.relevant
            or finding.date != target["date"]
            or finding.source_url not in urls
            or finding.kind not in {"strike", "closure"}
        ):
            return None
        return observation(
            finding.kind, {**target, "summary": finding.summary}, finding.source_url, "news"
        )
    if kind == "event":
        if not settings.get("TICKETMASTER_API_KEY"):
            return None
        event = http.request(
            "GET",
            "https://app.ticketmaster.com/discovery/v2/events/" + target["event_id"] + ".json",
            params={"apikey": settings.get("TICKETMASTER_API_KEY")},
        )
        if event.get("dates", {}).get("status", {}).get("code") not in {"cancelled", "postponed"}:
            return None
        return observation(
            "event_cancel", {**target, "summary": "Event cancelled or postponed"}, event["url"]
        )
    if kind in {"link", "price"}:
        link = db.links.find_one({"_id": target["link_id"]})
        if not link:
            return None
        if kind == "link":
            result = http.request("HEAD", link["url"], ttl=86400, public=True)
            if result["status_code"] in {403, 405}:
                result = http.request("GET", link["url"], ttl=86400, public=True)
            db.links.update_one(
                {"_id": link["_id"]},
                {
                    "$set": {
                        "validation": {
                            "status": "ok" if result["ok"] else "broken",
                            "ok": result["ok"],
                            "checked_at": now(),
                        }
                    }
                },
            )
            if result["ok"]:
                return None
            return observation(
                "dead_link", {**target, "summary": "Booking link is unavailable"}, link["url"]
            )
        # Prices come from an explicitly timestamped provider observation, never from scraping.
        price = link.get("price_hint", {})
        if not price.get("as_of"):
            return None
        previous = db.price_snapshots.find_one(
            {"meta.link_id": link["_id"], "currency": price["currency"]}, sort=[("ts", -1)]
        )
        if previous and previous.get("observed_at") == price["as_of"]:
            return None
        rows = list(
            db.price_snapshots.aggregate(
                [
                    {
                        "$match": {
                            "meta.link_id": link["_id"],
                            "currency": price["currency"],
                            "ts": {"$gte": now() - timedelta(days=7)},
                        }
                    },
                    {
                        "$setWindowFields": {
                            "sortBy": {"ts": 1},
                            "output": {
                                "average": {
                                    "$avg": "$amount",
                                    "window": {"documents": ["unbounded", "current"]},
                                }
                            },
                        }
                    },
                ]
            )
        )
        average = rows[-1]["average"] if rows else None
        db.price_snapshots.insert_one(
            {
                "ts": now(),
                "meta": {
                    "link_id": link["_id"],
                    "provider": link["provider"],
                    "kind": link["kind"],
                },
                "amount": price["amount"],
                "currency": price["currency"],
                "observed_at": price["as_of"],
            }
        )
        if average and price["amount"] < average:
            trip = db.trips.find_one({"_id": watch["trip_id"]})
            notice_id = "notice_price_drop_" + watch["_id"] + "_" + str(price["as_of"])
            db.alerts.update_one(
                {"_id": notice_id},
                {
                    "$setOnInsert": {
                        "user_id": trip["user_id"],
                        "trip_id": trip["_id"],
                        "disruption_id": notice_id,
                        "channel": "board",
                        "title": "A watched price dropped",
                        "body": f"{link['label']}: {price['amount']} {price['currency']}, below its seven-day average.",
                        "status": "sent",
                        "created_at": now(),
                        "source_url": link["url"],
                        "source_type": "provider",
                        "fetched_at": price["as_of"],
                    }
                },
                upsert=True,
            )
        if average and price["amount"] > average * 1.2:
            return observation(
                "price_spike",
                {
                    **target,
                    "amount": price["amount"],
                    "currency": price["currency"],
                    "baseline": average,
                    "summary": "Price increased over 20% versus the seven-day average",
                },
                link["url"],
            )
    return None
