"""The 18 scenarios of the Node-RED boiler flow, plus the cheapest-price heating and legionella start."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from logic.boiler import BoilerInput, BoilerState, decide, legionella_due
from settings import defaults

S = defaults()
S["use_instant"] = True


def run(settings=None, state=None, **inp):
    s = {**S, **(settings or {})}
    values = {"temp": 45.0, "cv_temp": 30.0, "solar_next_hour": 500.0, "holiday": False, "element_on": False,
              "in_price_slot": False}
    values.update(inp)
    state = state if state is not None else BoilerState()
    return decide(BoilerInput(**values), s, state), state


def test_no_sun_all_off():
    r, _ = run()
    assert (r.element, r.instant, r.valve_setpoint) == (False, False, 7)


def test_sun_heats():
    r, _ = run(solar_next_hour=4000)
    assert (r.element, r.element_reason, r.instant) == (True, "solar", False)


def test_cold_without_sun_instant_on():
    r, _ = run(temp=30)
    assert (r.element, r.instant) == (False, True)


def test_instant_not_used():
    r, _ = run({"use_instant": False}, temp=30)
    assert (r.instant, r.instant_reason) == (False, "not used")


def test_sun_has_priority_over_instant():
    r, _ = run(solar_next_hour=4000, temp=30)
    assert (r.element, r.instant, r.instant_reason) == (True, False, "element heats")


@pytest.mark.parametrize(
    ("temp", "element_on", "expected"),
    [(60, True, False), (57, False, False), (57, True, True), (54, False, True)],
)
def test_max_temp_with_restart_hysteresis(temp, element_on, expected):
    assert run(solar_next_hour=4000, temp=temp, element_on=element_on)[0].element is expected


def test_holiday_no_solar_heating():
    r, _ = run(solar_next_hour=4000, holiday=True)
    assert (r.element, r.element_reason) == (False, "on holiday")


def test_legionella_heats_without_sun_and_on_holiday_then_ends():
    state = BoilerState(legionella=True)
    r, state = run(state=state, holiday=True, temp=50)
    assert (r.element, r.element_reason) == (True, "legionella")
    r, state = run(state=state, temp=60, element_on=True)
    assert r.element is False and state.legionella is False


def test_valve():
    assert run(cv_temp=50)[0].valve_setpoint == 35
    assert run(cv_temp=40)[0].valve_setpoint == 7
    r, _ = run(cv_temp=None)
    assert r.valve_setpoint is None and r.valve_reason == "CV temperature unavailable"


def test_boiler_temp_unavailable_decides_nothing():
    r, _ = run(temp=None, solar_next_hour=4000)
    assert not r.available and r.reason == "boiler temperature unavailable"


def test_cheapest_price_slot():
    r, _ = run(in_price_slot=True, temp=40)
    assert (r.element, r.element_reason, r.target_temp) == (True, "cheapest price", 55)
    assert run(in_price_slot=True, temp=55, element_on=True)[0].element is False
    assert run(in_price_slot=True, holiday=True)[0].element is False


def test_legionella_start_once_on_its_day():
    tz = ZoneInfo("Europe/Amsterdam")
    sunday_morning = datetime(2026, 11, 22, 10, 59, tzinfo=tz)  # a Sunday
    state = BoilerState()
    assert not legionella_due(sunday_morning, S, state)
    assert legionella_due(sunday_morning.replace(hour=11, minute=0), S, state) and state.legionella
    state.legionella = False  # done
    assert not legionella_due(sunday_morning.replace(hour=15), S, state)  # not twice on the same day
    assert not legionella_due(datetime(2026, 11, 23, 12, tzinfo=tz), S, BoilerState())  # Monday
    assert not legionella_due(sunday_morning.replace(hour=12), {**S, "legionella_enabled": False}, BoilerState())
