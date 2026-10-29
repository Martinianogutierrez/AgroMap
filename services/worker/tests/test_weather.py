from datetime import date

from pipeline.weather import apply_rule, metric_value, season_window, synthetic_daily


def test_season_window_cross_year():
    start, end = season_window(2020, "soybean", lat=-33.0)
    assert start == date(2020, 10, 1)
    assert end == date(2021, 3, 31)


def test_season_window_north():
    start, end = season_window(2020, "soybean", lat=40.0)
    assert start.year == end.year == 2020


def test_apply_rule_above_mean():
    values = {2019: 100.0, 2020: 200.0, 2021: 300.0}
    dec = apply_rule(values, {"op": "above_mean", "tolerance_pct": 0})
    assert dec[2019].included is False
    assert dec[2021].included is True


def test_metric_precip():
    df = synthetic_daily(date(2020, 1, 1), date(2020, 1, 10), precip_scale=1.0, seed=1)
    v = metric_value(df, "precip_sum_mm")
    assert v > 0
