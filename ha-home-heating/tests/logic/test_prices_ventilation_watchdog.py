from datetime import datetime
from zoneinfo import ZoneInfo

from logic.prices import cheapest, in_plan, parse, plan_day
from logic.ventilation import boost
from logic.watchdog import Watchdog
from settings import defaults

S = defaults()
TZ = ZoneInfo("Europe/Amsterdam")


# ------------------------------------------------------------------------------------- prices

def zonneplan(start: datetime, prices_eur: list[float]) -> list[dict]:
    """Quarter-hourly Zonneplan forecast items."""
    return [
        {"start_date": datetime.fromtimestamp(start.timestamp() + i * 900, TZ).isoformat(),
         "price_tax_included": {"amount": round(p * 1e7)}}
        for i, p in enumerate(prices_eur)
    ]


def test_parse_zonneplan_quarters():
    start = datetime(2026, 11, 22, 0, 0, tzinfo=TZ)
    slots = parse(zonneplan(start, [0.30, 0.20, 0.25]), "start_date", "price_tax_included.amount", 1e7)
    assert [(b - a, round(p, 2)) for a, b, p in slots] == [(900, 0.30), (900, 0.20), (900, 0.25)]


def test_parse_nordpool_style():
    items = [{"start": "2026-11-22T00:00:00+01:00", "value": 0.31}, {"start": "2026-11-22T01:00:00+01:00", "value": 0.29}]
    slots = parse(items, "start", "value", 1)
    assert [b - a for a, b, _ in slots] == [3600, 3600]


def test_cheapest_two_hours_before_deadline():
    start = datetime(2026, 11, 22, 0, 0, tzinfo=TZ)
    prices = [0.30] * 96
    for i in (8, 9, 10, 11, 52, 53, 54, 55):  # 02:00-03:00 and 13:00-14:00 cheapest
        prices[i] = 0.10
    prices[80] = 0.01  # 20:00, after the deadline
    slots = parse(zonneplan(start, prices), "start_date", "price_tax_included.amount", 1e7)
    plan = plan_day(start.replace(minute=5), S, slots, solar_remaining_kwh=1.0)
    hours = sorted({datetime.fromtimestamp(a, TZ).hour for a, *_ in plan["slots"]})
    assert hours == [2, 13] and len(plan["slots"]) == 8
    assert in_plan(plan, datetime(2026, 11, 22, 13, 30, tzinfo=TZ).timestamp())
    assert not in_plan(plan, datetime(2026, 11, 22, 20, 0, tzinfo=TZ).timestamp())


def test_enough_solar_no_plan():
    plan = plan_day(datetime(2026, 11, 22, 0, 5, tzinfo=TZ), S, [], solar_remaining_kwh=8.0)
    assert plan["slots"] == [] and plan["reason"].startswith("enough solar")


def test_cheapest_only_full_slots_inside_window():
    slots = [(0, 3600, 0.1), (3600, 7200, 0.2), (7200, 10800, 0.05)]
    assert cheapest(slots, 0, 7200, 1) == [(0, 3600, 0.1)]


# ------------------------------------------------------------------------------------- ventilation

def test_ventilation_boost_with_hysteresis():
    assert boost(15, 23, S, False)[0] is True
    assert boost(9, 23, S, False)[0] is False  # outside too cold
    assert boost(24, 23, S, False)[0] is False  # outside warmer than inside
    assert boost(15, 21.5, S, False)[0] is False  # inside cool enough
    assert boost(9.8, 23, S, True)[0] is True  # hysteresis keeps it on
    assert boost(9.4, 23, S, True)[0] is False
    assert boost(22.6, 23, S, True)[0] is False  # outside nearly as warm as inside
    assert boost(None, 23, S, True)[0] is False


# ------------------------------------------------------------------------------------- watchdog

def test_alert_on_change_repeat_and_recovery():
    wd = Watchdog()
    assert wd.update({"boiler_temp": ("ok", "")}, 0, 24) == []
    alerts = wd.update({"boiler_temp": ("error", "unavailable")}, 60, 24)
    assert [(a.check, a.state, a.repeat) for a in alerts] == [("boiler_temp", "error", False)]
    assert wd.state == "error"
    assert wd.update({"boiler_temp": ("error", "unavailable")}, 3600, 24) == []  # not again within 24 h
    again = wd.update({"boiler_temp": ("error", "unavailable")}, 60 + 24 * 3600, 24)
    assert [(a.state, a.repeat) for a in again] == [("error", True)]
    back = wd.update({"boiler_temp": ("ok", "")}, 90000, 24)
    assert [(a.state, a.repeat) for a in back] == [("ok", False)] and wd.state == "ok"


def test_removed_check_recovers_and_store_roundtrip():
    wd = Watchdog()
    wd.update({"a": ("warning", "x")}, 0, 0)
    restored = Watchdog.from_dict(wd.to_store())
    assert restored.update({"a": ("warning", "x")}, 10, 0) == []  # no new alert after a restart
    assert [(a.check, a.state) for a in restored.update({}, 20, 0)] == [("a", "ok")]
