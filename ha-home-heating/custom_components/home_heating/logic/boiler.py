"""Boiler: element, doorstroomboiler and heat exchanger valve.

Element (electric heater in the boiler), in this order:
1. Legionella run: heat until legionella_temp, also without sun.
2. Solar: expected solar power next hour above solar_threshold, not on holiday, until max_temp.
3. Cheapest prices: in the planned cheapest slots when not enough solar is expected, not on holiday,
   until price_target_temp.
Once off because warm enough, the element only restarts below the target minus restart_hysteresis.

Doorstroomboiler (instant water heater), only when used: on below instant_below, unless the element
heats. They are never both on.

Valve: open when the CV water is warmer than the boiler (the heat pump heats the boiler), closed
otherwise (the boiler does not lose heat to the CV).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class BoilerInput:
    temp: float | None  # boiler temperature
    cv_temp: float | None  # CV water temperature
    solar_next_hour: float | None  # W
    holiday: bool
    element_on: bool | None  # current state of the element switch (None: unknown)
    in_price_slot: bool  # now is one of the planned cheapest slots


@dataclass
class BoilerState:
    legionella: bool = False
    legionella_started_date: str | None = None  # the run starts once on its day


@dataclass
class BoilerResult:
    available: bool
    reason: str = ""
    element: bool = False
    element_reason: str = ""
    instant: bool = False
    instant_reason: str = ""
    valve_setpoint: float | None = None
    valve_reason: str = ""
    target_temp: float | None = None


def legionella_due(now: datetime, s: dict, state: BoilerState) -> bool:
    """Start the weekly legionella run on its weekday, from its time. Once per day."""
    if not s["legionella_enabled"] or state.legionella:
        return False
    hour, minute = (int(p) for p in s["legionella_time"].split(":")[:2])
    today = now.date().isoformat()
    if (
        WEEKDAYS[now.weekday()] == s["legionella_weekday"]
        and (now.hour, now.minute) >= (hour, minute)
        and state.legionella_started_date != today
    ):
        state.legionella = True
        state.legionella_started_date = today
        return True
    return False


def decide(inp: BoilerInput, s: dict, state: BoilerState) -> BoilerResult:
    """Decide what the boiler should do. Updates `state` when the legionella run is done."""
    if inp.temp is None:
        # Without the boiler temperature no safe decision can be made
        return BoilerResult(False, "boiler temperature unavailable")

    r = BoilerResult(True)

    # 1. Element: why should it heat, and up to which temperature?
    if state.legionella:
        reason, target = "legionella", s["legionella_temp"]
    elif not inp.holiday and inp.solar_next_hour is not None and inp.solar_next_hour > s["solar_threshold"]:
        reason, target = "solar", s["max_temp"]
    elif not inp.holiday and inp.in_price_slot:
        reason, target = "cheapest price", s["price_target_temp"]
    else:
        reason, target = None, None

    if reason is None:
        r.element, r.element_reason = False, "on holiday" if inp.holiday else "no sun, no cheap slot"
    else:
        # Off at the target. Once off, only restart below the target minus the hysteresis
        restart_below = target - s["restart_hysteresis"]
        warm = inp.temp >= target or (not inp.element_on and inp.temp >= restart_below)
        r.target_temp = target
        if warm:
            r.element, r.element_reason = False, f"warm enough ({reason})"
        else:
            r.element, r.element_reason = True, reason
        if state.legionella and inp.temp >= s["legionella_temp"]:
            state.legionella = False  # legionella run done

    # 2. Doorstroomboiler: only when used, never together with the element
    if not s["use_instant"]:
        r.instant, r.instant_reason = False, "not used"
    elif r.element:
        r.instant, r.instant_reason = False, "element heats"
    elif inp.temp < s["instant_below"]:
        r.instant, r.instant_reason = True, f"boiler below {s['instant_below']:g} °C"
    else:
        r.instant, r.instant_reason = False, "boiler warm enough"

    # 3. Heat exchanger valve
    if inp.cv_temp is None:
        r.valve_reason = "CV temperature unavailable"
    elif inp.cv_temp > inp.temp:
        r.valve_setpoint, r.valve_reason = s["valve_open"], "open: CV water warmer than the boiler"
    else:
        r.valve_setpoint, r.valve_reason = s["valve_closed"], "closed: boiler warmer than the CV water"

    return r
