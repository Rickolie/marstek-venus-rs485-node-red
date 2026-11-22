"""Ventilation boost: ventilate at maximum to cool the house with outside air.

On when the outside temperature is above min_outside, but below the inside temperature,
and the inside temperature is above min_inside. Off again with a small hysteresis,
so it does not switch back and forth around the limits.
"""

from __future__ import annotations


def boost(outside: float | None, inside: float | None, s: dict, active: bool) -> tuple[bool, str]:
    """(boost active, reason)."""
    if outside is None or inside is None:
        return False, "temperature unavailable"

    h = s["vent_hysteresis"]
    if not active:
        if outside > s["vent_min_outside"] and outside < inside and inside > s["vent_min_inside"]:
            return True, "outside air cooler than inside"
        return False, "conditions not met"

    # Active: stay on until a condition is clearly no longer met
    if outside <= s["vent_min_outside"] - h:
        return False, "outside too cold"
    if outside >= inside - h:
        return False, "outside not cooler than inside"
    if inside <= s["vent_min_inside"] - h:
        return False, "inside cool enough"
    return True, "outside air cooler than inside"
