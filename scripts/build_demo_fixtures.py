"""Generate versioned, deterministic recorded responses and minimal Track 1 contracts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(folder, name, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    folder = ROOT / "fixtures" / "track1" / "lisbon"
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[-9.15, 38.70], [-9.13, 38.70], [-9.13, 38.72], [-9.15, 38.72], [-9.15, 38.70]]
        ],
    }
    places = [
        {
            "_id": "place_baixa",
            "name": "Baixa walking tour",
            "category": "activity",
            "indoor": False,
            "geo": {"type": "Point", "coordinates": [-9.139, 38.710]},
            "source_url": "https://www.visitlisboa.com/",
        },
        {
            "_id": "place_museum",
            "name": "Lisbon Story Centre",
            "category": "museum",
            "indoor": True,
            "geo": {"type": "Point", "coordinates": [-9.137, 38.707]},
            "source_url": "https://www.lisboastorycentre.pt/",
        },
        {
            "_id": "place_belem",
            "name": "Belém waterfront",
            "category": "activity",
            "indoor": False,
            "geo": {"type": "Point", "coordinates": [-9.216, 38.693]},
            "source_url": "https://www.visitlisboa.com/",
        },
        {
            "_id": "place_market",
            "name": "Time Out Market Lisboa",
            "category": "food",
            "indoor": True,
            "geo": {"type": "Point", "coordinates": [-9.146, 38.707]},
            "source_url": "https://www.timeoutmarket.com/lisboa/",
        },
    ]
    for place in places:
        place.update(
            {
                "source": "osm",
                "source_id": place["_id"],
                "business_status": "OPERATIONAL",
                "consensus": {"score": 0.8, "tags": ["fixture"]},
                "fixture": True,
            }
        )
    write(folder, "places", places)
    write(
        folder,
        "user",
        {
            "_id": "user_lisbon_fixture",
            "name": "Demo traveler",
            "email": "traveler@example.invalid",
            "notify": {"email": True, "voice": True},
            "fixture": True,
        },
    )
    write(
        folder,
        "trip",
        {
            "_id": "trip_lisbon",
            "user_id": "user_lisbon_fixture",
            "session_id": "ses_lisbon",
            "origin": {"name": "London"},
            "destination": {
                "name": "Lisbon",
                "country": "PT",
                "geo": {"type": "Point", "coordinates": [-9.139, 38.71]},
            },
            "dates": {"start": "2026-03-12", "end": "2026-03-17", "flexible": False},
            "travelers": 2,
            "profile": {"interests": ["food", "history"], "pace": "relaxed"},
            "status": "planned",
            "board_id": "b_lisbon",
            "legs": [
                {
                    "kind": "flight",
                    "carrier": "TP",
                    "number": "1351",
                    "from": "LHR",
                    "to": "LIS",
                    "dep_at": "2026-03-12T09:00:00+00:00",
                    "arr_at": "2026-03-12T11:30:00+00:00",
                }
            ],
            "fixture": True,
        },
    )
    links = [
        {
            "_id": "link_museum",
            "trip_id": "trip_lisbon",
            "item_ref": "place_museum",
            "kind": "monument_ticket",
            "provider": "official",
            "url": "https://www.lisboastorycentre.pt/",
            "label": "Lisbon Story Centre tickets",
            "for_date": "2026-03-14",
            "validation": {"status": "ok", "ok": True},
            "price_hint": {"amount": 8, "currency": "EUR", "as_of": "2026-03-10T12:00:00Z"},
            "fixture": True,
        }
    ]
    write(folder, "links", links)
    days = []
    for index, date in enumerate(["2026-03-12", "2026-03-13", "2026-03-14", "2026-03-15"]):
        slots = []
        for position, place in enumerate(places):
            slots.append(
                {
                    "slot_id": f"slot_{index}_{position}",
                    "time": f"{13 + position}:00",
                    "item": {
                        "place_id": place["_id"],
                        "name": place["name"],
                        "why": "Illustrative fixture itinerary",
                        "event_id": "fixture_event" if position == 3 else None,
                        "timed_ticket": position == 1,
                        "link_ids": ["link_museum"] if position == 1 and index == 2 else [],
                        "source_url": place["source_url"],
                        "source_type": "fixture",
                        "fetched_at": "2026-03-10T12:00:00Z",
                        "opinion_chips": [],
                    },
                }
            )
        days.append(
            {
                "date": date,
                "theme": [
                    "Arrival and food",
                    "City discoveries",
                    "Baixa and museums",
                    "Waterfront",
                ][index],
                "area_geo": polygon,
                "slots": slots,
            }
        )
    write(
        folder,
        "board",
        {
            "_id": "b_lisbon",
            "trip_id": "trip_lisbon",
            "user_id": "user_lisbon_fixture",
            "version": 1,
            "share_slug": "lisbon-fixture-demo",
            "title": "A few days in Lisbon",
            "summary": "A synthetic itinerary for disruption testing. No real bookings or live travel claims.",
            "days": days,
            "bookings": [
                {
                    "link_id": "link_museum",
                    "kind": "monument_ticket",
                    "label": "Lisbon Story Centre tickets",
                    "url": links[0]["url"],
                    "status": "todo",
                }
            ],
            "fixture": True,
        },
    )
    for kind, disruption, url in [
        ("flight", "flight_delay", "https://www.flytap.com/"),
        ("train", "strike", "https://www.cp.pt/"),
        ("weather", "weather", "https://open-meteo.com/"),
        ("area", "strike", "https://www.metrolisboa.pt/"),
        ("venue", "closure", "https://www.visitlisboa.com/"),
        ("event", "event_cancel", "https://www.ticketmaster.pt/"),
        ("link", "dead_link", "https://www.lisboastorycentre.pt/"),
        ("price", "price_spike", "https://www.lisboastorycentre.pt/"),
    ]:
        write(
            ROOT / "fixtures" / "http",
            kind,
            {
                "kind": disruption,
                "summary": "SIMULATED " + disruption.replace("_", " "),
                "minutes": 180,
                "rain_probability": 90,
                "wind_kmh": 55,
                "amount": 12,
                "currency": "EUR",
                "baseline": 8,
                "evidence": [
                    {
                        "source_url": url,
                        "source_type": "fixture",
                        "summary": "Synthetic recorded response; not a real disruption",
                        "fetched_at": "2026-03-10T12:00:00Z",
                    }
                ],
            },
        )


if __name__ == "__main__":
    main()
