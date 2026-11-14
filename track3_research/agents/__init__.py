"""Agent registry: research_jobs.kind → ResearchAgent subclass."""
from __future__ import annotations

from importlib import import_module

_REGISTRY = {
    "opinions": ("opinions", "OpinionScout"),
    "places": ("places", "PlaceFinder"),
    "stay": ("stay", "StayFinder"),
    "getting_there": ("getting_there", "GettingThereFinder"),
    "transit": ("transit", "TransitFinder"),
    "tickets": ("tickets", "TicketFinder"),
    "nightlife": ("nightlife", "NightlifeFinder"),
    "restaurants": ("restaurants", "RestaurantFinder"),
    "link_resolve": ("link_job", "LinkResolveAgent"),
}


def agent_for(kind: str):
    mod, cls = _REGISTRY[kind]
    return getattr(import_module(f"track3_research.agents.{mod}"), cls)
