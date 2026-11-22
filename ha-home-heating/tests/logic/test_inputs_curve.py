import pytest
from logic.curve import crossing, interpolate, table_from_rows
from logic.inputs import Source, select
from settings import DEFAULTS

TABLE = table_from_rows(DEFAULTS["heating_table"])  # -20:50, -10:44, 0:38, 10:27, 20:18


@pytest.mark.parametrize(
    ("outside", "water"),
    [(-25, 50), (-20, 50), (-15, 47), (-10, 44), (-2.5, 39.5), (0, 38), (5, 32.5), (10, 27), (15, 22.5), (20, 18), (25, 18)],
)
def test_lookup_table(outside, water):
    assert interpolate(TABLE, outside) == pytest.approx(water)


def test_table_reaches_24_degrees_at_13_3():
    assert crossing(TABLE, 24) == pytest.approx(13.33, abs=0.01)


def test_master_used_when_valid():
    r = select([Source("sensor.a", "5.5"), Source("sensor.b", "6")])
    assert (r.value, r.source, r.status, r.problems) == (5.5, "sensor.a", "master", [])


def test_backup_takes_over():
    r = select([Source("sensor.a", "unavailable"), Source("sensor.b", "6"), Source("sensor.c", "7")])
    assert (r.value, r.source, r.status) == (6, "sensor.b", "backup")
    assert r.problems == ["sensor.a is unavailable"]


def test_missing_and_non_numeric():
    r = select([Source("sensor.gone", None), Source("sensor.text", "abc")])
    assert r.value is None and r.status == "unavailable"
    assert r.problems == ["sensor.gone does not exist", "sensor.text is abc"]


def test_stale_master():
    r = select([Source("sensor.a", "5", age_s=7200), Source("sensor.b", "6", age_s=60)], max_age_s=3600)
    assert r.source == "sensor.b" and "did not report for 120 min" in r.problems[0]


def test_deviation_reported():
    r = select([Source("sensor.a", "5"), Source("sensor.b", "8"), Source("sensor.c", "5.5")], max_deviation=2)
    assert r.value == 5 and r.problems == ["sensor.b differs 3.0 from master sensor.a"]


def test_text_input():
    assert select([Source("input_boolean.h", "on")], numeric=False).value == "on"
    assert select([], numeric=False).status == "not configured"


def test_rolling_average_time_weighted():
    from logic.inputs import rolling_average

    hour = 3600
    now = 100 * hour
    # 10 °C for 18 hours, then 20 °C for the last 6 hours; an older sample before the window counts from the start
    samples = [(now - 30 * hour, 10.0), (now - 6 * hour, 20.0)]
    assert rolling_average(samples, now, 24) == pytest.approx(12.5)
    assert rolling_average([(now - 2 * hour, 5.0)], now, 24) is None  # too little data
