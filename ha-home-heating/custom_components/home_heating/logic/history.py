"""Heat pump history: daily averages and their relation to the outside temperature.

Per day: average outside temperature, water target temperature, power, energy, running hours and COP
(COP = 0 excluded). Only days on which the heat pump has been used.

Two sources, the exact state history wins:
- state history: time-weighted, only as far back as the recorder keeps it
- long-term statistics: hourly mean/min/max, kept forever. Fills in the older days.

Result: averages, a straight line per value (value = slope * outside + intercept, used as the
History heating curve) and the averages per whole °C outside temperature.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, tzinfo

KEYS = ("outside", "water", "power", "cop")
DAY = 86400
HOUR = 3600


def _number(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


# ------------------------------------------------------------------------------------- state history


def segments(states: list[tuple[float, str]], now: float) -> list[tuple[float, float, float]]:
    """States -> time segments (start, end, value). A state is valid until the next state change;
    unknown / unavailable states become gaps."""
    points = sorted(states)
    result = []
    for i, (t, state) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else now
        value = _number(state)
        if value is not None and end > t:
            result.append((t, end, value))
    return result


def history_per_day(segs: list[tuple[float, float, float]], tz: tzinfo, min_run_power: float, include=lambda v: True) -> dict:
    """Time-weighted sums per local day: {day: {sum, s, covered_s, running_s}}."""
    days: dict = defaultdict(lambda: {"sum": 0.0, "s": 0.0, "covered_s": 0.0, "running_s": 0.0})
    for start, end, value in segs:
        t = start
        while t < end:
            local = datetime.fromtimestamp(t, tz)
            next_day = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
            stop = min(end, next_day)
            day = days[local.date().isoformat()]
            day["covered_s"] += stop - t
            if include(value):
                day["sum"] += value * (stop - t)
                day["s"] += stop - t
            if value > min_run_power:
                day["running_s"] += stop - t
            t = stop
    return days


def days_from_history(history: dict, now: float, tz: tzinfo, s: dict) -> dict:
    today = datetime.fromtimestamp(now, tz).date().isoformat()
    per = {
        key: history_per_day(segments(history.get(key, []), now), tz, s["min_run_power"],
                             (lambda v: v > 0) if key == "cop" else (lambda v: True))  # COP 0 not averaged
        for key in KEYS
    }
    avg = lambda d: d["sum"] / d["s"] if d and d["s"] > 0 else None  # noqa: E731
    covered = lambda d: d is not None and d["covered_s"] >= s["min_day_coverage"] * DAY  # noqa: E731
    days = {}
    for key, p in per["power"].items():
        o = per["outside"].get(key)
        if key == today or not covered(p) or not covered(o):
            continue
        power = avg(p)  # hours without data count as the measured hours
        days[key] = {
            "source": "history",
            "outside_temp_avg": _round(avg(o), 1),
            "water_target_temp_avg": _round(avg(per["water"].get(key)), 1),
            "power_avg": _round(power, 0),
            "energy": _round(power * 24 / 1000, 2),
            "running_hours": _round(p["running_s"] / HOUR, 1),
            "cop_avg": _round(avg(per["cop"].get(key)), 2),
            "ran": p["running_s"] > 0,
        }
    return days


# ------------------------------------------------------------------------------------- statistics


def days_from_statistics(statistics: dict, now: float, tz: tzinfo, s: dict) -> dict:
    """Hourly statistics -> per local day. The hourly COP mean includes the 0 values while the heat
    pump is off: hours with min > 0 ran the full hour and give a clean COP; only when a day has none,
    hours with mean > 0 are used."""
    today = datetime.fromtimestamp(now, tz).date().isoformat()

    def hours(rows):
        days = defaultdict(list)
        for row in rows or []:
            mean = _number(row.get("mean"))
            if mean is not None:
                day = datetime.fromtimestamp(float(row["start"]), tz).date().isoformat()
                days[day].append((mean, _number(row.get("min")), _number(row.get("max"))))
        return days

    per = {key: hours(statistics.get(key)) for key in KEYS}
    enough = lambda h: h is not None and len(h) >= s["min_day_coverage"] * 24  # noqa: E731
    days = {}
    for key, p in per["power"].items():
        o = per["outside"].get(key)
        if key == today or not enough(p) or not enough(o):
            continue
        c = per["cop"].get(key, [])
        cop_hours = [h for h in c if h[1] is not None and h[1] > 0] or [h for h in c if h[0] > 0]
        power = _mean([h[0] for h in p])
        days[key] = {
            "source": "statistics",
            "outside_temp_avg": _round(_mean([h[0] for h in o]), 1),
            "water_target_temp_avg": _round(_mean([h[0] for h in per["water"].get(key, [])]), 1),
            "power_avg": _round(power, 0),
            "energy": _round(power * 24 / 1000, 2),
            "running_hours": sum(1 for h in p if h[0] > s["min_run_power"]),  # approximation
            "cop_avg": _round(_mean([h[0] for h in cop_hours]), 2),
            "ran": any(h[2] is not None and h[2] > s["min_run_power"] for h in p),
        }
    return days


# ------------------------------------------------------------------------------------- result


def fit(days: list[dict], key: str) -> dict | None:
    """Least squares line: value = slope * outside_temp + intercept."""
    pts = [(d["outside_temp_avg"], d[key]) for d in days if d.get(key) is not None and d.get("outside_temp_avg") is not None]
    n = len(pts)
    if n < 2:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    slope = sxy / sxx
    return {"slope": round(slope, 3), "intercept": round(my - slope * mx, 2), "days": n}


def calculate(history: dict, statistics: dict, now: float, tz: tzinfo, s: dict) -> dict | None:
    """Summary of all used days, or None when there are none."""
    merged = {**days_from_statistics(statistics, now, tz, s), **days_from_history(history, now, tz, s)}
    all_days = [{"date": d, **merged[d]} for d in sorted(merged)]
    used = [d for d in all_days if d["energy"] is not None and d["energy"] >= s["min_day_energy"] and d["ran"]]
    if not used:
        return None

    def average(rows, key, digits):
        return _round(_mean([r[key] for r in rows if r.get(key) is not None]), digits)

    groups = defaultdict(list)
    for d in used:
        groups[math.floor(d["outside_temp_avg"] + 0.5)].append(d)  # .5 rounds up, as before
    curve = [
        {
            "outside_temp": t,
            "days": len(rows),
            "water_target_temp": average(rows, "water_target_temp_avg", 1),
            "power": average(rows, "power_avg", 0),
            "energy": average(rows, "energy", 2),
            "cop": average(rows, "cop_avg", 2),
        }
        for t, rows in sorted(groups.items())
    ]
    return {
        "updated": datetime.fromtimestamp(now, tz).isoformat(),
        "first_day": used[0]["date"],
        "last_day": used[-1]["date"],
        "days_used": len(used),
        "days_from_history": sum(1 for d in used if d["source"] == "history"),
        "days_from_statistics": sum(1 for d in used if d["source"] == "statistics"),
        "days_skipped": len(all_days) - len(used),
        "averages": {
            "outside_temp_avg": average(used, "outside_temp_avg", 1),
            "water_target_temp_avg": average(used, "water_target_temp_avg", 1),
            "power_avg": average(used, "power_avg", 0),
            "energy": average(used, "energy", 2),
            "cop_avg": average(used, "cop_avg", 2),
        },
        "fit": {
            "water_target_temp": fit(used, "water_target_temp_avg"),
            "power": fit(used, "power_avg"),
            "cop": fit(used, "cop_avg"),
        },
        "curve": curve,
        "days": used,
    }
