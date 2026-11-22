"""The history gives the same result as the Node-RED History flow it replaces.

fixtures/: 20 days of synthetic statistics + 5 days of state history (with a day without heating,
a sensor gap and a water sensor without statistics), and the result of the Node-RED flow on it.
"""

import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from logic.history import calculate, fit, segments
from settings import defaults

FIXTURES = Path(__file__).parent / "fixtures"
E = {"outside": "sensor.sparrow_p60_heat_pump_outside_temperature",
     "water": "sensor.sparrow_p60_heat_pump_water_target_temperature",
     "power": "sensor.sparrow_p60_heat_pump", "cop": "sensor.heatpump_cop_not_below_0"}


def load(name):
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


def clean(rows):
    return [{k: v for k, v in r.items() if v is not None and k != "ran"} for r in rows]


def test_same_as_node_red_flow():
    data, node_red = load("hist-input"), load("hist-nodered")
    history = {k: [(p["lu"], p["s"]) for p in data["history"].get(e, [])] for k, e in E.items()}
    statistics = {k: [{**r, "start": r["start"] / 1000} for r in data["statistics"].get(e, [])] for k, e in E.items()}
    result = calculate(history, statistics, data["now"] / 1000, ZoneInfo("UTC"), defaults())
    for key in ("days_used", "days_from_history", "days_from_statistics", "days_skipped", "first_day", "last_day",
                "averages", "fit"):
        assert result[key] == node_red[key], key
    assert clean(result["days"]) == clean(node_red["days"])
    assert clean(result["curve"]) == clean(node_red["curve"])
    assert result["days_used"] == 18 and result["days_from_history"] == 4


def test_no_used_days():
    assert calculate({}, {}, 1e9, ZoneInfo("UTC"), defaults()) is None


def test_segments_skip_unavailable():
    assert segments([(0, "1"), (10, "unavailable"), (20, "3")], 30) == [(0, 10, 1.0), (20, 30, 3.0)]


def test_fit_line():
    days = [{"outside_temp_avg": x, "v": 40 - 0.5 * x} for x in (-5, 0, 5, 10)]
    assert fit(days, "v") == {"slope": -0.5, "intercept": 40.0, "days": 4}
    assert fit(days[:1], "v") is None
