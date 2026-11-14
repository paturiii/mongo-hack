"""Trip slot schema + question templates (MASTERPLAN §7.1). Scoring = impact × uncertainty."""

# id: (impact, required, type, prompt, why, options)
# destination/origin options are built per trip at ask time (dynamic_options in graph.py).
SLOTS: dict[str, tuple] = {
    "destination": (11, True, "single", "Where do you want to go?",
                    "Researched cities plan in minutes; any other city works but takes longer", []),
    "origin": (10, True, "single", "Where are you traveling from?",
               "Required: flights and transfers are linked from your starting city", []),
    "dates": (10, True, "date_range", "Which dates are you going?",
              "Club nights, timed tickets and prices all depend on exact dates", []),
    "interests": (9, True, "multi", "What are you into on this trip?",
                  "Decides which research agents run", [
                      ("food", "Food"), ("nightlife", "Nightlife"), ("history", "History & sights"),
                      ("art", "Art & museums"), ("nature", "Nature & day trips"), ("beaches", "Beaches")]),
    "budget_band": (9, True, "single", "What's your budget?", "Changes stays, restaurants and transport", [
        ("budget", "Budget — hostels, street food"), ("mid", "Mid-range — comfy, not flashy"),
        ("splurge", "Splurge — the good stuff")]),
    "pace": (8, True, "single", "What pace do you want?", "Changes how many things we plan per day", [
        ("relaxed", "Relaxed — 2–3 things a day"), ("balanced", "Balanced — 3–4 a day"),
        ("packed", "Packed — see everything")]),
    "travelers": (7, True, "number", "How many people are going?", "Sets ticket and table sizes", []),
    "stay_vibe": (7, False, "single", "Where do you want to stay?", "Picks the neighborhood your days start from", [
        ("historic", "Historic & walkable"), ("lively", "Lively — bars on the doorstep"),
        ("quiet", "Quiet & local"), ("beach", "Near the beach")]),
    "nightlife_intensity": (7, False, "single", "How late do you go out?", "Decides which nights get club or bar plans", [
        ("early", "Early nights"), ("drinks", "A few drinks"), ("big_night_out", "One or two big nights"),
        ("every_night", "Every night")]),
    "dietary": (5, False, "multi", "Any dietary needs?", "Filters restaurant picks", [
        ("none", "None"), ("vegetarian", "Vegetarian"), ("vegan", "Vegan"), ("gluten_free", "Gluten-free"),
        ("halal", "Halal")]),
    "dealbreakers": (4, False, "multi", "Anything you want to avoid?", "We penalize plans that hit these", [
        ("queues", "Long queues"), ("early_starts", "Early starts"), ("crowds", "Crowds"), ("walking", "Lots of walking")]),
    "must_sees": (4, False, "free", "Anything you must see or do?", "Locked into the plan first", []),
    "stay_type": (3, False, "single", "What kind of place to stay?", "Filters stay links", [
        ("hotel", "Hotel"), ("apartment", "Apartment"), ("hostel", "Hostel")]),
    "booking_ahead": (3, False, "single", "Book ahead or stay flexible?", "Orders the booking checklist", [
        ("book_all", "Book everything now"), ("flexible", "Keep it flexible")]),
    "mobility": (2, False, "single", "Any mobility needs?", "Avoids steep or stair-heavy routes", [
        ("none", "None"), ("limited", "Limited walking"), ("wheelchair", "Wheelchair")]),
    "transport_pref": (2, False, "single", "Preferred way to get around between cities?", "Ranks transit links", [
        ("train", "Train"), ("bus", "Bus"), ("flight", "Flight"), ("cheapest", "Whatever's cheapest")]),
}
MAX_PER_BATCH = 6
MIN_IMPACT_BATCH1 = 5


def relevant(slot: str, known: dict) -> bool:
    interests = known.get("interests") or []
    if slot == "nightlife_intensity":
        return "nightlife" in interests
    if slot == "dietary":
        return "food" in interests
    return True


def is_known(value) -> bool:
    return value not in (None, "", [], {})


def pick(known: dict, asked: set[str], batch: int) -> list[str]:
    """Top unknown slots by impact × uncertainty. Batch 2 only re-asks required gaps."""
    cands = []
    for sid, (impact, required, *_rest) in SLOTS.items():
        if is_known(known.get(sid)) or not relevant(sid, known):
            continue
        if batch == 1 and impact < MIN_IMPACT_BATCH1:
            continue
        if batch >= 2 and not required:  # batch 2: only required gaps (skipped, or cut by MAX_PER_BATCH)
            continue
        cands.append((impact * 1.0, sid))  # uncertainty is 1.0 for unknown slots, 0 for known ones
    cands.sort(reverse=True)
    return [sid for _, sid in cands[:MAX_PER_BATCH]]


def question(sid: str, hint: str | None = None, options: list[tuple[str, str]] | None = None) -> dict:
    _impact, required, qtype, prompt, why, static = SLOTS[sid]
    q = {"id": sid, "prompt": prompt, "type": qtype, "why": why, "required": required,
         "options": [{"id": o, "label": label} for o, label in (options or static)], "prefilled_from_memory": False}
    if hint:
        q["hint"] = hint
    return q
