"""Dynamic prices: read a price list and pick the cheapest hours.

The price list comes from an attribute of a price sensor: a list of items with a start time and
a price. The keys are settings, so it works with Zonneplan, Nordpool, EasyEnergy and others.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _get(item: dict, path: str):
    """Value at a dotted path, e.g. 'price_tax_included.amount'."""
    value = item
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _timestamp(value) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)  # ms or s
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def parse(items: list, time_key: str, value_key: str, factor: float) -> list[tuple[float, float, float]]:
    """Price slots [(start s, end s, price per kWh)], sorted. The slot length follows from the data."""
    points = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start, price = _timestamp(_get(item, time_key)), _get(item, value_key)
        try:
            price = float(price) / (factor or 1)
        except (TypeError, ValueError):
            continue
        if start is not None:
            points.append((start, price))
    points.sort()
    slots = []
    for i, (start, price) in enumerate(points):
        if i + 1 < len(points):
            end = points[i + 1][0]  # until the next price
        elif i > 0:
            end = start + (start - points[i - 1][0])  # last one: as long as the one before
        else:
            end = start + 3600
        slots.append((start, end, price))
    return slots


def cheapest(slots: list[tuple[float, float, float]], start: float, end: float, hours: float) -> list[tuple[float, float, float]]:
    """The cheapest slots inside [start, end] that together last at least `hours`."""
    inside = [slot for slot in slots if slot[0] >= start and slot[1] <= end]
    chosen, total = [], 0.0
    for slot in sorted(inside, key=lambda s: (s[2], s[0])):
        if total >= hours * 3600:
            break
        chosen.append(slot)
        total += slot[1] - slot[0]
    return sorted(chosen)


def plan_day(
    now: datetime,
    s: dict,
    slots: list[tuple[float, float, float]],
    solar_remaining_kwh: float | None,
) -> dict:
    """Plan of the day for heating the boiler on the cheapest prices.

    Only needed when there will not be enough solar: the remaining solar forecast of today is below
    the set minimum, or there is no solar forecast at all.
    """
    plan = {"date": now.date().isoformat(), "slots": [], "reason": ""}
    if solar_remaining_kwh is not None and solar_remaining_kwh >= s["price_min_solar_kwh"]:
        plan["reason"] = f"enough solar expected ({solar_remaining_kwh:.1f} kWh)"
        return plan
    hour, minute = (int(p) for p in s["price_deadline"].split(":")[:2])
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)
    chosen = cheapest(slots, now.timestamp(), deadline.timestamp(), s["price_hours"])
    if not chosen:
        plan["reason"] = "no prices until the deadline"
        return plan
    plan["slots"] = [[a, b, p] for a, b, p in chosen]
    plan["reason"] = (
        "not enough solar expected" if solar_remaining_kwh is not None else "no solar forecast"
    ) + f": {len(chosen)} cheapest slots before {s['price_deadline']}"
    return plan


def in_plan(plan: dict | None, t: float) -> bool:
    return any(a <= t < b for a, b, *_ in (plan or {}).get("slots", []))
