"""Deterministic buy-box filters. Cheap, run before any LLM call."""
from __future__ import annotations

from .models import BuyBox, Lot


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def hard_filter(lot: Lot, box: BuyBox) -> tuple[bool, list[str]]:
    """Return (passed, reasons_failed). Unknown values pass, so the LLM can
    judge them from the description instead of silently dropping the lot."""
    fails: list[str] = []

    if box.year_min and lot.year and lot.year < box.year_min:
        fails.append(f"year {lot.year} < {box.year_min}")
    if box.year_max and lot.year and lot.year > box.year_max:
        fails.append(f"year {lot.year} > {box.year_max}")
    if box.max_miles and lot.odometer is not None and lot.odometer > box.max_miles:
        fails.append(f"{lot.odometer:,} mi > {box.max_miles:,}")
    if box.makes and lot.make and _norm(lot.make) not in {_norm(m) for m in box.makes}:
        fails.append(f"make {lot.make} not in buy box")
    if box.models and lot.model and _norm(lot.model) not in {_norm(m) for m in box.models}:
        fails.append(f"model {lot.model} not in buy box")
    if box.title_types and lot.title_type:
        if not any(_norm(t) in _norm(lot.title_type) for t in box.title_types):
            fails.append(f"title '{lot.title_type}' not allowed")
    if box.conditions and lot.condition:
        if not any(_norm(c) in _norm(lot.condition) for c in box.conditions):
            fails.append(f"condition '{lot.condition}' not allowed")
    damage = f"{_norm(lot.primary_damage)} {_norm(lot.secondary_damage)}"
    for bad in box.excluded_damage:
        if _norm(bad) and _norm(bad) in damage:
            fails.append(f"excluded damage: {bad}")
    if box.require_keys and lot.has_keys is False:
        fails.append("no keys")
    if box.locations and lot.location:
        if not any(_norm(l) in _norm(lot.location) for l in box.locations):
            fails.append(f"location {lot.location} outside buy box")
    if box.max_budget and lot.buy_now is None and lot.current_bid and lot.current_bid > box.max_budget:
        fails.append(f"current bid ${lot.current_bid:,.0f} already over budget")

    return (not fails, fails)
