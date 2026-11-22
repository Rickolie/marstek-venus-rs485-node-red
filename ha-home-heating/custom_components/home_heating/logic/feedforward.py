"""Heat pump feedforward: the water temperature from the outside temperature.

Steps, in this order:
1. Outside temperature: the current measurement, or the weather forecast.
2. Cooling: when the outside temperature will be above the set value, cool along the cooling table.
3. Heating curve: the manual lookup table, or the straight line from the history.
4. Simple day schedule: in mild weather heat at a fixed water temperature from the start time
   until the inside temperature is reached, then off for the rest of the day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

from .curve import interpolate, table_from_rows

HOUR = 3600


@dataclass
class FeedforwardInput:
    now: datetime  # local, timezone aware
    outside: float | None  # current outside temperature
    outside_avg_24h: float | None  # measured average of the last 24 hours
    inside: float | None
    forecast: list[tuple[float, float]] | None = None  # [(timestamp s, °C)], sorted
    forecast_fetched: float | None = None  # timestamp s
    history_fit: dict | None = None  # fit from the history: {"water_target_temp": {...}, "power": .., "cop": ..}


@dataclass
class FeedforwardState:
    """Remembered between runs (and restarts)."""

    stopped_day: str | None = None  # heating day on which the simple schedule stopped
    cooling: bool = False


@dataclass
class FeedforwardResult:
    available: bool
    reason: str = ""
    demand: str = "off"  # "heat", "cool" or "off"
    mode: str = ""  # "Curve", "Simple", "Cooling"
    water_temp: float | None = None
    outside_temp: float | None = None
    outside_source: str = ""
    outside_temp_used: float | None = None
    outside_temp_avg_used: float | None = None
    forecast_temp: float | None = None
    forecast_bias: float | None = None
    curve_source: str = ""
    slope: float | None = None
    intercept: float | None = None
    expected_power: float | None = None
    expected_cop: float | None = None
    heating_day: str | None = None
    details: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------- forecast


def forecast_at(forecast: list[tuple[float, float]], t: float) -> float:
    """Temperature at time t, linear between the hourly forecast points."""
    if t <= forecast[0][0]:
        return forecast[0][1]
    for (t0, v0), (t1, v1) in pairwise(forecast):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return forecast[-1][1]


def forecast_average(forecast: list[tuple[float, float]], start: float, hours: float) -> float:
    """Average temperature over [start, start + hours], sampled every 15 minutes."""
    if not hours > 0:
        return forecast_at(forecast, start)
    steps = max(1, round(hours * 4))
    total = sum(forecast_at(forecast, start + (i + 0.5) * hours * HOUR / steps) for i in range(steps))
    return total / steps


def forecast_max(forecast: list[tuple[float, float]], start: float, hours: float) -> float:
    """Highest forecast temperature in [start, start + hours]."""
    values = [v for t, v in forecast if start <= t <= start + hours * HOUR]
    values.append(forecast_at(forecast, start))
    return max(values)


def forecast_recent(inp: FeedforwardInput, max_age_h: float) -> bool:
    return bool(
        inp.forecast
        and inp.forecast_fetched is not None
        and inp.now.timestamp() - inp.forecast_fetched < max_age_h * HOUR
    )


# ------------------------------------------------------------------------------------- helpers


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _line(fit: dict | None, x: float) -> float | None:
    if not fit or fit.get("slope") is None or fit.get("intercept") is None:
        return None
    return fit["slope"] * x + fit["intercept"]


def heating_day(now: datetime, start_time: str) -> str:
    """Heating day that started at start_time. Before that time the previous day still counts."""
    hour, minute = (int(p) for p in (start_time or "00:00").split(":")[:2])
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start.isoformat()


# ------------------------------------------------------------------------------------- calculate


def calculate(inp: FeedforwardInput, s: dict, state: FeedforwardState) -> FeedforwardResult:
    """Feedforward water temperature. Updates `state` (simple schedule stop, cooling)."""
    if inp.outside is None:
        return FeedforwardResult(False, "outside temperature unavailable")

    r = FeedforwardResult(True, outside_temp=inp.outside)
    now = inp.now.timestamp()

    # 1. Outside temperature: current or forecast ------------------------------------------
    r.outside_source = s["outside_source"]
    r.outside_temp_used = inp.outside
    r.outside_temp_avg_used = inp.outside_avg_24h
    use_forecast = s["outside_source"] == "Forecast"
    if use_forecast and not forecast_recent(inp, s["forecast_max_age_hours"]):
        r.outside_source = "Current (forecast unavailable)"
        use_forecast = False
    bias = 0.0
    if use_forecast:
        # Optional: shift the forecast by the difference with the measurement right now
        if s["forecast_bias_correction"]:
            bias = inp.outside - forecast_at(inp.forecast, now)
        forecast_temp = forecast_average(inp.forecast, now + s["forecast_lead_time"] * HOUR, s["forecast_window"]) + bias
        weight = min(max(s["forecast_weight"], 0), 100) / 100
        r.forecast_temp = forecast_temp
        r.forecast_bias = bias
        r.outside_temp_used = inp.outside + weight * (forecast_temp - inp.outside)
        r.outside_temp_avg_used = forecast_average(inp.forecast, now, s["simple_forecast_hours"]) + bias

    x = r.outside_temp_used

    # 2. Cooling ------------------------------------------------------------------------------
    if s["cooling_enabled"]:
        # "Will be above": the highest forecast temperature of the coming hours, else the current one
        coming = (
            forecast_max(inp.forecast, now, s["cooling_forecast_hours"]) + bias if use_forecast else inp.outside
        )
        r.details["cooling_trigger_temp"] = _round(coming, 1)
        if coming >= s["cooling_from_outside"]:
            state.cooling = True
        elif coming < s["cooling_from_outside"] - s["cooling_hysteresis"]:
            state.cooling = False
    else:
        state.cooling = False

    if state.cooling:
        cooling_table = table_from_rows(s["cooling_table"])
        water = interpolate(cooling_table, x)
        r.mode, r.demand, r.curve_source = "Cooling", "cool", "Cooling table"
        r.water_temp = max(water, s["cooling_min_water"])
        return _finish(r, inp)

    # 3. Heating curve ----------------------------------------------------------------------------
    hist = (inp.history_fit or {}).get("water_target_temp")
    if s["curve_source"] == "History" and _line(hist, x) is not None:
        r.curve_source, r.slope, r.intercept = "History", hist["slope"], hist["intercept"]
        water = _line(hist, x)
    else:
        r.curve_source = "Manual (history unavailable)" if s["curve_source"] == "History" else "Manual"
        water = interpolate(table_from_rows(s["heating_table"]), x)
    r.mode, r.demand = "Curve", "heat"
    r.water_temp = min(max(water, s["min_water_temp"]), s["max_water_temp"])

    # 4. Simple day schedule (mild weather) --------------------------------------------------------
    avg = r.outside_temp_avg_used
    if s["simple_enabled"] and avg is not None and avg >= s["simple_outside_temp"]:
        r.mode = "Simple"
        r.water_temp = s["simple_water_temp"]
        r.heating_day = heating_day(inp.now, s["simple_start_time"])
        if state.stopped_day == r.heating_day:
            r.demand = "off"  # stopped earlier today
        elif inp.inside is not None and inp.inside >= s["simple_stop_inside_temp"]:
            state.stopped_day = r.heating_day  # reached: off for the rest of the heating day
            r.demand = "off"
        # inside temperature unavailable: keep heating

    return _finish(r, inp)


def _finish(r: FeedforwardResult, inp: FeedforwardInput) -> FeedforwardResult:
    fit = inp.history_fit or {}
    x = r.outside_temp_used
    r.expected_power = _round(_line(fit.get("power"), x), 0)
    r.expected_cop = _round(_line(fit.get("cop"), x), 2)
    for key, digits in (("water_temp", 1), ("outside_temp_used", 1), ("outside_temp_avg_used", 1),
                        ("forecast_temp", 1), ("forecast_bias", 1)):
        setattr(r, key, _round(getattr(r, key), digits))
    return r
