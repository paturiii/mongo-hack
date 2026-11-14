"""Generate /fixtures/lisbon/*.json (APP-02). Deterministic; opinions are clearly marked as fixtures.

    python scripts/make_fixtures.py
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "lisbon"
CITY = "Lisbon"
random.seed(7)


def d(s: str) -> dict:
    return {"$date": s}


START = datetime(2027, 3, 12, tzinfo=timezone.utc)
NOW = "2026-11-14T12:00:00Z"
USER = "usr_fixture_ana"
SES = "ses_fixture_lisbon"
TRIP = "trip_fixture_lisbon"
BOARD = "b_fixture_lisbon"

# name, category, lng, lat, neighborhood, indoor, price_level, tags
PLACES = [
    ("Mosteiro dos Jerónimos", "sight", -9.2068, 38.6979, "Belém", True, 2, ["book ahead", "go early"]),
    ("Torre de Belém", "sight", -9.2160, 38.6916, "Belém", False, 2, ["photo spot", "long queue"]),
    ("Pastéis de Belém", "food", -9.2033, 38.6975, "Belém", True, 1, ["pastel de nata", "takeaway faster"]),
    ("MAAT", "museum", -9.1946, 38.6960, "Belém", True, 2, ["architecture", "sunset"]),
    ("Padrão dos Descobrimentos", "sight", -9.2056, 38.6936, "Belém", False, 1, ["viewpoint"]),
    ("Castelo de São Jorge", "sight", -9.1335, 38.7139, "Alfama", False, 2, ["views", "go early"]),
    ("Miradouro de Santa Luzia", "sight", -9.1301, 38.7118, "Alfama", False, 1, ["viewpoint", "free"]),
    ("Sé de Lisboa", "sight", -9.1335, 38.7098, "Alfama", True, 1, ["cathedral"]),
    ("Tasca do Chico", "bar", -9.1450, 38.7123, "Bairro Alto", True, 1, ["fado vadio", "authentic", "arrive early"]),
    ("Clube de Fado", "bar", -9.1316, 38.7101, "Alfama", True, 3, ["fado", "dinner show"]),
    ("Mesa de Frades", "bar", -9.1265, 38.7128, "Alfama", True, 3, ["fado", "intimate", "book ahead"]),
    ("Feira da Ladra", "activity", -9.1260, 38.7146, "Alfama", False, 1, ["flea market", "tue/sat only"]),
    ("Panteão Nacional", "sight", -9.1247, 38.7149, "Alfama", True, 1, ["rooftop views"]),
    ("Time Out Market", "food", -9.1459, 38.7071, "Cais do Sodré", True, 2, ["touristy", "variety"]),
    ("Pink Street", "bar", -9.1443, 38.7072, "Cais do Sodré", False, 2, ["nightlife", "crowded"]),
    ("Musicbox", "club", -9.1437, 38.7069, "Cais do Sodré", True, 2, ["live music", "late"]),
    ("Lux Frágil", "club", -9.1210, 38.7140, "Santa Apolónia", True, 3, ["techno", "door policy", "arrive before 2am"]),
    ("Park Bar", "bar", -9.1470, 38.7106, "Bairro Alto", False, 2, ["rooftop", "sunset"]),
    ("Pavilhão Chinês", "bar", -9.1480, 38.7155, "Príncipe Real", True, 2, ["quirky", "cocktails"]),
    ("Cervejaria Ramiro", "food", -9.1356, 38.7206, "Intendente", True, 3, ["seafood", "queue", "prego for dessert"]),
    ("Taberna da Rua das Flores", "food", -9.1430, 38.7104, "Chiado", True, 2, ["no reservations", "local favorite"]),
    ("A Cevicheria", "food", -9.1491, 38.7163, "Príncipe Real", True, 3, ["ceviche", "queue"]),
    ("Manteigaria", "food", -9.1435, 38.7108, "Chiado", True, 1, ["pastel de nata", "better than belém"]),
    ("O Trevo", "food", -9.1428, 38.7104, "Chiado", True, 1, ["bifana", "cheap eats"]),
    ("Zé da Mouraria", "food", -9.1349, 38.7167, "Mouraria", True, 2, ["huge portions", "lunch only"]),
    ("Livraria Bertrand", "activity", -9.1420, 38.7108, "Chiado", True, 1, ["oldest bookshop"]),
    ("Elevador de Santa Justa", "sight", -9.1394, 38.7121, "Baixa", False, 2, ["tourist trap", "walk up instead"]),
    ("Praça do Comércio", "sight", -9.1366, 38.7076, "Baixa", False, 1, ["free", "riverfront"]),
    ("Rua Augusta Arch", "sight", -9.1369, 38.7085, "Baixa", False, 1, ["viewpoint"]),
    ("LX Factory", "activity", -9.1784, 38.7034, "Alcântara", True, 2, ["sunday market", "street art"]),
    ("Museu Calouste Gulbenkian", "museum", -9.1541, 38.7372, "Avenidas Novas", True, 2, ["hidden gem", "gardens"]),
    ("Museu Nacional do Azulejo", "museum", -9.1136, 38.7247, "Xabregas", True, 1, ["tiles", "underrated"]),
    ("Oceanário de Lisboa", "museum", -9.0937, 38.7635, "Parque das Nações", True, 3, ["kids", "book ahead"]),
    ("Miradouro da Senhora do Monte", "sight", -9.1328, 38.7192, "Graça", False, 1, ["best sunset", "free"]),
    ("Tram 28 (Martim Moniz stop)", "transit_hub", -9.1358, 38.7158, "Mouraria", False, 1, ["pickpockets", "board at start"]),
    ("Palácio Nacional da Pena", "sight", -9.3904, 38.7876, "Sintra", False, 3, ["timed entry", "book ahead", "go early"]),
    ("Quinta da Regaleira", "sight", -9.3960, 38.7963, "Sintra", False, 2, ["initiation well", "go early"]),
    ("Praia de Carcavelos", "beach", -9.3368, 38.6779, "Carcavelos", False, 1, ["surf", "train from Cais do Sodré"]),
    ("Memmo Alfama", "stay", -9.1310, 38.7110, "Alfama", True, 3, ["rooftop pool", "quiet"]),
    ("The Lumiares", "stay", -9.1452, 38.7128, "Bairro Alto", True, 3, ["nightlife on doorstep", "noisy weekends"]),
]
SOURCES = [
    ("reddit", "r/lisbon", "https://www.reddit.com/r/lisbon/comments/fixture{n}/"),
    ("reddit", "r/travel", "https://www.reddit.com/r/travel/comments/fixture{n}/"),
    ("forum", "TripAdvisor Lisbon forum", "https://www.tripadvisor.com/ShowTopic-fixture{n}.html"),
    ("forum", "Rick Steves forum", "https://community.ricksteves.com/travel-forum/fixture{n}"),
    ("blog", "travel blog", "https://example-travel-blog.com/lisbon-fixture{n}"),
    ("review", "Google reviews", "https://maps.google.com/?cid=fixture{n}"),
    ("video", "YouTube", "https://www.youtube.com/watch?v=fixture{n}"),
    ("news", "local news", "https://example-news.pt/fixture{n}"),
]


def place_docs():
    out = []
    for i, (name, cat, lng, lat, hood, indoor, price, tags) in enumerate(PLACES):
        out.append({
            "_id": f"pl_lis_{i:02d}", "trip_id": None, "source": "fixture", "source_id": f"fx{i:02d}",
            "name": name, "city": CITY, "category": cat, "indoor": indoor,
            "geo": {"type": "Point", "coordinates": [lng, lat]}, "neighborhood": hood,
            "address": f"{hood}, Lisboa", "price_level": price,
            "hours": {"mon": "09:00-18:00"} if cat == "museum" else {}, "rating": round(4.0 + random.random() * 0.8, 1),
            "business_status": "OPERATIONAL",
            "consensus": {"score": 0.0, "mentions": 0, "by_source": {}, "tags": tags, "opinion_ids": []},
            "updated_at": d(NOW),
        })
    return out


def opinion_docs(places):
    out, n = [], 0
    for p in places:
        picks = random.sample(SOURCES, 2)
        for st, src, url in picks:
            n += 1
            tag = random.choice(p["consensus"]["tags"])
            con = tag in ("tourist trap", "touristy", "crowded", "long queue", "pickpockets", "noisy weekends")
            sent = round(-0.4 - random.random() * 0.4, 2) if con else round(0.4 + random.random() * 0.55, 2)
            out.append({
                "_id": f"op_lis_{n:03d}", "place_id": p["_id"], "city": CITY, "source_type": st, "source_name": src,
                "source_url": url.format(n=n), "source_title": f"[FIXTURE] {p['name']} thread",
                "summary": f"[FIXTURE] Travelers on {src} say {p['name']}: {tag}.",
                "quote": f"{tag} — worth knowing before {p['name']}"[:120],
                "stance": "con" if con else ("tip" if tag in ("go early", "book ahead", "arrive early", "arrive before 2am") else "pro"),
                "sentiment": sent, "tags": [tag], "posted_at": d("2025-06-01T00:00:00Z"),
                "weight": {"reddit": 1.0, "forum": 0.9, "blog": 0.7, "review": 0.8, "video": 0.5, "news": 0.9}[st],
                "fetched_at": d(NOW), "expires_at": d("2027-12-31T00:00:00Z"),
            })
            p["consensus"]["opinion_ids"].append(out[-1]["_id"])
            p["consensus"]["by_source"][st] = p["consensus"]["by_source"].get(st, 0) + 1
            p["consensus"]["mentions"] += 1
        sents = [o["sentiment"] for o in out if o["place_id"] == p["_id"]]
        p["consensus"]["score"] = round(sum(sents) / len(sents), 2)
    return out


LINKS = [
    ("lk_flight", "flight", "google_flights", "https://www.google.com/travel/flights?q=LHR%20to%20LIS%202027-03-12", "Flights London → Lisbon (Mar 12)", False, None),
    ("lk_transfer", "transfer", "official", "https://www.metrolisboa.pt/en/", "Airport → Alfama by metro (red + green line)", True, None),
    ("lk_stay_alfama", "stay", "booking", "https://www.booking.com/searchresults.html?ss=Alfama%2C+Lisbon&checkin=2027-03-12&checkout=2027-03-17&group_adults=2", "Stays in Alfama (Mar 12–17, 2 guests)", False, "pl_lis_38"),
    ("lk_stay_bairro", "stay", "airbnb", "https://www.airbnb.com/s/Bairro-Alto--Lisbon/homes?checkin=2027-03-12&checkout=2027-03-17&adults=2", "Airbnb in Bairro Alto", False, "pl_lis_39"),
    ("lk_jeronimos", "monument_ticket", "official", "https://www.museusemonumentos.pt/en/", "Jerónimos Monastery tickets (official)", True, "pl_lis_00"),
    ("lk_belem", "monument_ticket", "official", "https://www.museusemonumentos.pt/en/", "Belém Tower tickets (official)", True, "pl_lis_01"),
    ("lk_castelo", "monument_ticket", "official", "https://castelodesaojorge.pt/en/", "Castelo de São Jorge tickets (official)", True, "pl_lis_05"),
    ("lk_pena", "monument_ticket", "official", "https://www.parquesdesintra.pt/en/", "Pena Palace timed entry (official)", True, "pl_lis_35"),
    ("lk_regaleira", "monument_ticket", "official", "https://www.regaleira.pt/en", "Quinta da Regaleira tickets (official)", True, "pl_lis_36"),
    ("lk_sintra_train", "train", "official", "https://www.cp.pt/passageiros/en", "Train Rossio → Sintra (CP)", True, None),
    ("lk_cascais_train", "train", "official", "https://www.cp.pt/passageiros/en", "Train Cais do Sodré → Carcavelos (CP)", True, None),
    ("lk_lux", "club_ticket", "ra", "https://ra.co/clubs/2463", "Lux Frágil events (Resident Advisor)", False, "pl_lis_16"),
    ("lk_musicbox", "club_ticket", "official", "https://www.musicboxlisboa.com/", "Musicbox lineup (official)", True, "pl_lis_15"),
    ("lk_clube_fado", "restaurant", "official", "https://www.clube-de-fado.com/", "Clube de Fado dinner reservation", True, "pl_lis_09"),
    ("lk_mesa_frades", "restaurant", "official", "https://www.mesadefrades.com/", "Mesa de Frades reservation", True, "pl_lis_10"),
    ("lk_ramiro", "restaurant", "official", "https://www.cervejariaramiro.pt/", "Cervejaria Ramiro (walk-in, queue)", True, "pl_lis_19"),
    ("lk_cevicheria", "restaurant", "official", "https://www.acevicheria.pt/", "A Cevicheria (walk-in)", True, "pl_lis_21"),
    ("lk_gulbenkian", "monument_ticket", "official", "https://gulbenkian.pt/museu/en/", "Gulbenkian Museum tickets (official)", True, "pl_lis_30"),
    ("lk_azulejo", "monument_ticket", "official", "https://www.museusemonumentos.pt/en/", "Tile Museum tickets (official)", True, "pl_lis_31"),
    ("lk_oceanario", "monument_ticket", "official", "https://www.oceanario.pt/en", "Oceanário tickets (official)", True, "pl_lis_32"),
    ("lk_maat", "monument_ticket", "official", "https://www.maat.pt/en", "MAAT tickets (official)", True, "pl_lis_03"),
    ("lk_tram28", "bus", "official", "https://www.carris.pt/en/", "Tram 28 + Viva Viagem card (Carris)", True, "pl_lis_34"),
    ("lk_lxfactory", "tour", "official", "https://lxfactory.com/en/", "LX Factory (official)", True, "pl_lis_29"),
    ("lk_timeout", "restaurant", "official", "https://www.timeoutmarket.com/lisboa/en/", "Time Out Market", True, "pl_lis_13"),
    ("lk_return", "flight", "google_flights", "https://www.google.com/travel/flights?q=LIS%20to%20LHR%202027-03-17", "Flights Lisbon → London (Mar 17)", False, None),
    ("lk_pastéis", "restaurant", "official", "https://pasteisdebelem.pt/en/", "Pastéis de Belém", True, "pl_lis_02"),
    ("lk_manteigaria", "restaurant", "official", "https://manteigaria.com/", "Manteigaria", True, "pl_lis_22"),
    ("lk_panteao", "monument_ticket", "official", "https://www.museusemonumentos.pt/en/", "Panteão Nacional tickets (official)", True, "pl_lis_12"),
    ("lk_se", "monument_ticket", "official", "https://www.sedelisboa.pt/", "Lisbon Cathedral (official)", True, "pl_lis_07"),
    ("lk_ferry", "transfer", "official", "https://ttsl.pt/en/", "Ferry Cais do Sodré ↔ Cacilhas (Transtejo)", True, None),
]


def link_docs():
    return [{
        "_id": lid, "trip_id": TRIP, "item_ref": ref or lid, "kind": kind, "provider": prov, "url": url, "label": label,
        "is_official": off, "price_hint": None, "for_date": None, "party_size": 2,
        "validation": {"status": 200, "checked_at": d(NOW), "ok": True}, "created_at": d(NOW),
    } for lid, kind, prov, url, label, off, ref in LINKS]


DAYS = [
    ("Arrival · Baixa & Chiado", [("16:00", 27, ["lk_transfer"]), ("17:30", 22, ["lk_manteigaria"]), ("20:00", 20, []), ("22:30", 8, [])]),
    ("Belém", [("09:30", 0, ["lk_jeronimos"]), ("11:30", 2, ["lk_pastéis"]), ("12:30", 1, ["lk_belem"]), ("15:00", 3, ["lk_maat"]), ("20:00", 29, ["lk_lxfactory"])]),
    ("Alfama & fado", [("09:00", 5, ["lk_castelo"]), ("11:00", 6, []), ("13:00", 24, []), ("16:00", 31, ["lk_azulejo"]), ("21:00", 10, ["lk_mesa_frades"])]),
    ("Sintra day trip", [("08:30", 35, ["lk_sintra_train", "lk_pena"]), ("13:30", 36, ["lk_regaleira"]), ("20:30", 19, ["lk_ramiro"]), ("23:59", 16, ["lk_lux"])]),
    ("Gulbenkian & goodbye", [("10:00", 30, ["lk_gulbenkian"]), ("13:00", 21, ["lk_cevicheria"]), ("17:30", 33, []), ("19:00", 17, [])]),
]


def board_doc(places, opinions):
    days = []
    for i, (theme, slots) in enumerate(DAYS):
        day_slots = []
        for j, (time, pi, link_ids) in enumerate(slots):
            p = places[pi]
            ops = [o for o in opinions if o["place_id"] == p["_id"]][:2]
            day_slots.append({
                "slot_id": f"d{i + 1}s{j + 1}", "time": time,
                "item": {"place_id": p["_id"], "name": p["name"], "geo": p["geo"], "category": p["category"],
                         "why": ops[0]["summary"].replace("[FIXTURE] ", "") if ops else "",
                         "opinion_chips": [{"text": o["quote"], "source_type": o["source_type"], "url": o["source_url"]} for o in ops],
                         "link_ids": link_ids, "est_cost": {"amount": 10 * p["price_level"], "currency": "EUR"}},
            })
        days.append({"date": d((START + timedelta(days=i)).strftime("%Y-%m-%dT00:00:00Z")), "theme": theme,
                     "area_geo": None, "weather_hint": None, "slots": day_slots})
    links = {l[0]: l for l in LINKS}
    order = ["lk_flight", "lk_return", "lk_transfer", "lk_stay_alfama", "lk_pena", "lk_lux", "lk_mesa_frades",
             "lk_jeronimos", "lk_castelo", "lk_sintra_train", "lk_gulbenkian"]
    hints = {"lk_pena": "timed entry — sells out, book now", "lk_lux": "check lineup for Mar 15", "lk_mesa_frades": "small room — book 1 week ahead"}
    bookings = [{"link_id": lid, "kind": links[lid][1], "label": links[lid][4], "url": links[lid][3],
                 "status": "todo", "deadline_hint": hints.get(lid)} for lid in order]
    bookings[0]["status"] = "user_marked_booked"
    return {
        "_id": BOARD, "trip_id": TRIP, "user_id": USER, "version": 1, "share_slug": "lisbon-mar-fx01",
        "title": "Lisbon · Mar 12–17 · food & nightlife", "summary": "5 days clustered by neighborhood: Belém, Alfama, a Sintra day trip, fado and one big club night.",
        "checklist_progress": {"booked": 1, "total": len(bookings)}, "days": days, "bookings": bookings,
        "updated_at": d(NOW),
    }


def write(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"  {name}: {len(data) if isinstance(data, list) else 1}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    places = place_docs()
    opinions = opinion_docs(places)
    write("user.json", {"_id": USER, "email": "ana.fixture@example.com", "name": "Ana (fixture)", "plan": "free",
                        "password_hash": "scrypt$fixture$not-a-real-hash", "api_key_hashes": [], "connected_clients": ["native"],
                        "notify": {"email": True, "voice": True}, "created_at": d(NOW)})
    write("session.json", {"_id": SES, "user_id": USER, "surface": "native", "thread_id": SES, "trip_id": TRIP, "status": "ready",
                           "messages": [{"role": "user", "content": "5 days in Lisbon in March, food and nightlife, flying TP1351 from London", "at": d(NOW)},
                                        {"role": "assistant", "content": "Lisbon in March, great pick. A few quick questions.", "at": d(NOW)}],
                           "created_at": d(NOW), "expires_at": d("2027-12-31T00:00:00Z")})
    write("questions.json", {"_id": "q_fixture_lisbon_1", "session_id": SES, "batch": 1, "status": "answered",
                             "items": [
                                 {"id": "budget_band", "prompt": "What's your budget?", "type": "single", "why": "Changes stays and restaurants", "required": True,
                                  "options": [{"id": "budget", "label": "Budget"}, {"id": "mid", "label": "Mid-range"}, {"id": "splurge", "label": "Splurge"}]},
                                 {"id": "pace", "prompt": "What pace do you want?", "type": "single", "why": "Changes how many items per day", "required": True,
                                  "options": [{"id": "relaxed", "label": "Relaxed — 2–3 things a day"}, {"id": "packed", "label": "Packed — see everything"}]}],
                             "answers": {"budget_band": "mid", "pace": "relaxed"}, "answered_at": d(NOW)})
    write("trip.json", {"_id": TRIP, "user_id": USER, "session_id": SES,
                        "origin": {"name": "London", "geo": {"type": "Point", "coordinates": [-0.1276, 51.5072]}},
                        "destination": {"name": CITY, "country": "Portugal", "geo": {"type": "Point", "coordinates": [-9.1393, 38.7223]}},
                        "dates": {"start": d("2027-03-12T00:00:00Z"), "end": d("2027-03-17T00:00:00Z"), "flexible": False}, "travelers": 2,
                        "legs": [{"kind": "flight", "carrier": "TP", "number": "TP1351", "from": "LHR", "to": "LIS",
                                  "dep_at": d("2027-03-12T10:05:00Z"), "arr_at": d("2027-03-12T12:45:00Z")}],
                        "profile": {"budget_band": "mid", "pace": "relaxed", "interests": ["food", "nightlife"], "dealbreakers": [],
                                    "vibe": ["historic", "lively"], "dietary": [], "mobility": None, "nightlife_intensity": "big_night_out"},
                        "status": "ready_for_research", "board_id": None})
    kinds = [("opinions", {"theme": "food"}), ("opinions", {"theme": "nightlife"}), ("opinions", {"theme": "hidden gems"}),
             ("opinions", {"theme": "things to skip"}), ("opinions", {"theme": "neighborhoods"}), ("places", {"category": "sight"}),
             ("places", {"category": "food"}), ("places", {"category": "bar"}), ("stay", {}), ("getting_there", {"origin": "London"}),
             ("transit", {}), ("tickets", {}), ("nightlife", {}), ("restaurants", {})]
    write("research_jobs.json", [{"_id": f"job_fx_{i:02d}", "trip_id": TRIP, "kind": k,
                                  "params": {"city": CITY, "source_types": ["reddit", "forum", "blog", "review", "news"],
                                             "dates": {"start": "2027-03-12", "end": "2027-03-17"}, **p},
                                  "status": "done" if i % 3 else "queued", "attempts": 0, "claimed_by": None, "claimed_at": None,
                                  "result_refs": [], "error": None, "created_at": d(NOW)} for i, (k, p) in enumerate(kinds)])
    write("places.json", places)
    write("opinions.json", opinions)
    write("links.json", link_docs())
    write("board.json", board_doc(places, opinions))
    write("memories.json", [
        {"_id": "mem_fx_1", "user_id": USER, "kind": "pref", "key": "pace", "value": "relaxed", "text": "Prefers a relaxed pace", "confidence": 0.9, "source": "answer", "updated_at": d(NOW)},
        {"_id": "mem_fx_2", "user_id": USER, "kind": "pref", "key": "budget_band", "value": "mid", "text": "Usually travels mid-range", "confidence": 0.9, "source": "answer", "updated_at": d(NOW)},
        {"_id": "mem_fx_3", "user_id": USER, "kind": "episode", "key": None, "value": None, "text": "Porto 2026: loved tascas and fado, found the Douro day trip rushed", "confidence": 0.8, "source": "rating", "updated_at": d(NOW)},
        {"_id": "mem_fx_4", "user_id": USER, "kind": "feedback", "key": "rebook_mode", "value": "train", "text": "Prefers trains over buses when rebooking", "confidence": 0.7, "source": "disruption_choice", "updated_at": d(NOW)},
    ])
    write("refine_requests.json", [
        {"_id": "rr_fx_user", "trip_id": TRIP, "source": "user", "disruption_id": None, "instruction": "make day 3 a beach day",
         "scope": {"days": ["2027-03-14"], "slot_ids": []}, "constraints": {}, "status": "queued", "result_board_version": None, "created_at": d(NOW)},
        {"_id": "rr_fx_dis", "trip_id": TRIP, "source": "disruption", "disruption_id": "dis_fx_strike", "instruction": "metro strike in Baixa on Mar 14: avoid metro, walk or taxi",
         "scope": {"days": ["2027-03-14"], "slot_ids": []}, "constraints": {"indoor_only": False}, "status": "queued", "result_board_version": None, "created_at": d(NOW)},
    ])
    write("watches.json", [
        {"_id": "w_fx_flight", "trip_id": TRIP, "kind": "flight", "target": {"carrier": "TP", "number": "TP1351", "date": "2027-03-12"},
         "target_key": "flight:TP1351:2027-03-12", "next_check_at": d(NOW), "interval_s": 3600, "active": True},
        *[{"_id": f"w_fx_weather_{i}", "trip_id": TRIP, "kind": "weather",
           "target": {"area_geo": None, "date": (START + timedelta(days=i)).strftime("%Y-%m-%d")},
           "target_key": f"weather:{(START + timedelta(days=i)).strftime('%Y-%m-%d')}",
           "next_check_at": d(NOW), "interval_s": 21600, "active": True} for i in range(5)],
        {"_id": "w_fx_pena", "trip_id": TRIP, "kind": "venue", "target": {"place_id": "pl_lis_35"}, "target_key": "venue:pl_lis_35",
         "next_check_at": d(NOW), "interval_s": 21600, "active": True},
        {"_id": "w_fx_lux", "trip_id": TRIP, "kind": "event", "target": {"place_id": "pl_lis_16"}, "target_key": "event:pl_lis_16",
         "next_check_at": d(NOW), "interval_s": 21600, "active": True},
    ])
    write("signals.json", [
        {"ts": d(NOW), "meta": {"watch_id": "w_fx_flight", "kind": "flight", "provider": "simulator"},
         "payload": {"status": "delayed", "delay_min": 180}, "hash": "fx-flight-delay-180"},
        {"ts": d(NOW), "meta": {"watch_id": "w_fx_weather_2", "kind": "weather", "provider": "open-meteo"},
         "payload": {"rain_prob": 0.85, "wind_kmh": 40}, "hash": "fx-weather-d3"},
    ])
    write("disruptions.json", [
        {"_id": "dis_fx_strike", "trip_id": TRIP, "kind": "strike", "severity": 3, "detected_at": d(NOW),
         "evidence": [{"source_url": "https://example-news.pt/fixture-metro-strike", "source_type": "news", "summary": "[FIXTURE] Metro strike announced for Mar 14"}],
         "affected": {"days": ["2027-03-14"], "slot_ids": ["d3s1", "d3s2"], "link_ids": []},
         "options": [{"id": "walk", "label": "Walk/taxi instead, keep the plan", "actions": [{"type": "notice"}]},
                     {"id": "reorder", "label": "Swap day 3 and day 5", "actions": [{"type": "replan", "instruction": "swap day 3 and 5"}]}],
         "status": "open", "chosen_option": None, "resolved_at": None},
    ])
    write("alerts.json", [
        {"_id": "al_fx_strike", "user_id": USER, "trip_id": TRIP, "disruption_id": "dis_fx_strike", "channel": "board",
         "title": "Metro strike on Mar 14", "body": "Your Alfama morning uses the metro. Walk or take a taxi, or swap days.",
         "audio_url": None, "status": "pending", "created_at": d(NOW)},
    ])
    print("fixtures written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
