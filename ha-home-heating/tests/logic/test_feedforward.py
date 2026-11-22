"""The scenarios of the Node-RED feedforward flow, plus cooling."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from logic.feedforward import FeedforwardInput, FeedforwardState, calculate, heating_day
from settings import defaults

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 22, 10, 30, tzinfo=TZ)
HOUR = 3600
HISTORY = {"water_target_temp": {"slope": -0.8, "intercept": 45}, "power": {"slope": -40, "intercept": 900},
           "cop": {"slope": 0.05, "intercept": 3.25}}


def falling_forecast(start=10):
    """10 °C at the current hour, 1 °C less every hour."""
    t0 = NOW.replace(minute=0).timestamp()
    return [(t0 + i * HOUR, start - i) for i in range(48)]


def run(settings=None, state=None, **inp):
    s = defaults()
    s["simple_outside_temp"] = 30  # out of the way, unless a test sets it
    s.update(settings or {})
    values = {"now": NOW, "outside": 10.0, "outside_avg_24h": 8.0, "inside": 20.0}
    values.update(inp)
    state = state if state is not None else FeedforwardState()
    return calculate(FeedforwardInput(**values), s, state), state


def test_outside_unavailable():
    r, _ = run(outside=None)
    assert not r.available and r.reason == "outside temperature unavailable"


def test_manual_table():
    r, _ = run(outside=5.0)
    assert (r.mode, r.demand, r.curve_source, r.water_temp) == ("Curve", "heat", "Manual", 32.5)


def test_limits():
    assert run({"max_water_temp": 45}, outside=-15.0)[0].water_temp == 45
    assert run({"min_water_temp": 20}, outside=25.0)[0].water_temp == 20
    assert run({"min_water_temp": 15}, outside=25.0)[0].water_temp == 18


def test_history_curve_and_fallback():
    r, _ = run({"curve_source": "History"}, history_fit=HISTORY)
    assert (r.curve_source, r.water_temp, r.expected_power, r.expected_cop) == ("History", 37, 500, 3.75)
    r, _ = run({"curve_source": "History"})
    assert (r.curve_source, r.water_temp) == ("Manual (history unavailable)", 27)


def test_forecast_lead_and_window():
    # average of 3 .. 5 hours ahead, 30 minutes past the hour: 10 - 4.5 = 5.5
    r, _ = run({"outside_source": "Forecast"}, forecast=falling_forecast(), forecast_fetched=NOW.timestamp())
    assert (r.outside_source, r.forecast_temp, r.outside_temp_used) == ("Forecast", 5.5, 5.5)
    assert r.outside_temp_avg_used == pytest.approx(-2.5, abs=0.1)  # next 24 hours


def test_forecast_weight_and_bias():
    fc = {"forecast": falling_forecast(), "forecast_fetched": NOW.timestamp()}
    r, _ = run({"outside_source": "Forecast", "forecast_weight": 50}, **fc)
    assert r.outside_temp_used == 7.8  # halfway 10 and 5.5
    r, _ = run({"outside_source": "Forecast", "forecast_bias_correction": True}, outside=12.0, **fc)
    assert (r.forecast_bias, r.forecast_temp) == (2.5, 8)  # measured 12, forecast now 9.5


def test_old_forecast_falls_back_to_current():
    old = NOW.timestamp() - 4 * HOUR
    r, _ = run({"outside_source": "Forecast"}, forecast=falling_forecast(), forecast_fetched=old)
    assert (r.outside_source, r.outside_temp_used) == ("Current (forecast unavailable)", 10)


def test_simple_schedule_stop_and_stay_off():
    s = {"simple_outside_temp": 5, "simple_stop_inside_temp": 21.5}
    r, state = run(s)
    assert (r.mode, r.demand, r.water_temp) == ("Simple", "heat", 24)
    r, state = run(s, state, inside=21.6)
    assert r.demand == "off" and state.stopped_day == heating_day(NOW, "00:00")
    assert run(s, state, inside=20.0)[0].demand == "off"  # still off today
    assert run(s, state, inside=None)[0].demand == "off"
    tomorrow = NOW + timedelta(days=1)
    assert run(s, state, now=tomorrow)[0].demand == "heat"  # a new heating day


def test_simple_schedule_inside_unavailable_keeps_heating():
    assert run({"simple_outside_temp": 5}, inside=None)[0].demand == "heat"


def test_simple_schedule_below_threshold_uses_curve():
    assert run({"simple_outside_temp": 9}, outside_avg_24h=8.0)[0].mode == "Curve"
    assert run({"simple_outside_temp": 9}, outside_avg_24h=None)[0].mode == "Curve"


def test_heating_day_before_start_time_is_previous_day():
    assert heating_day(NOW, "11:00") == (NOW - timedelta(days=1)).replace(hour=11, minute=0).isoformat()
    assert heating_day(NOW, "10:00") == NOW.replace(hour=10, minute=0).isoformat()


def test_cooling_from_forecast_with_hysteresis():
    hot = [(t, v + 20) for t, v in falling_forecast(start=10)]  # 30 °C now, falling
    s = {"cooling_enabled": True, "outside_source": "Forecast", "forecast_lead_time": 0, "forecast_window": 0}
    r, state = run(s, outside=30.0, forecast=hot, forecast_fetched=NOW.timestamp())
    # 30 - 0.5 = 29.5 °C: cooling table 22 °C -> 19, 40 °C -> 22
    assert (r.mode, r.demand, r.water_temp) == ("Cooling", "cool", pytest.approx(20.2, abs=0.05))
    warm = [(t, 23.0) for t, _ in hot]
    assert run(s, state, outside=23.0, forecast=warm, forecast_fetched=NOW.timestamp())[0].mode == "Cooling"  # hysteresis
    assert run(s, state, outside=21.0, forecast=[(t, 21.0) for t, _ in hot],
               forecast_fetched=NOW.timestamp())[0].mode != "Cooling"


def test_cooling_min_water_and_current_temperature():
    r, _ = run({"cooling_enabled": True}, outside=24.0)
    assert (r.mode, r.water_temp) == ("Cooling", pytest.approx(19.3, abs=0.05))
    assert run({"cooling_enabled": True, "cooling_min_water": 21}, outside=24.0)[0].water_temp == 21
    assert run({"cooling_enabled": False}, outside=30.0)[0].mode != "Cooling"
