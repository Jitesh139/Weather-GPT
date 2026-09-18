"""Researcher dashboard data services.

The statistics here are the reason these endpoints deliberately don't go
near the LLM - they're arithmetic, so they're tested as arithmetic.
"""
import pytest

from services import research


# --- statistics ------------------------------------------------------


def test_summary_stats():
    assert research.summary_stats([1.0, 2.0, 6.0]) == {"mean": 3.0, "min": 1.0, "max": 6.0, "count": 3}


def test_summary_stats_on_empty_range():
    assert research.summary_stats([]) == {"mean": None, "min": None, "max": None, "count": 0}


def test_rolling_average_only_emits_once_the_window_is_full():
    # Leading positions are None so the chart doesn't draw a jumpy,
    # misleading line over the first few points.
    assert research.rolling_average([1.0, 2.0, 3.0, 4.0], 3) == [None, None, 2.0, 3.0]


def test_rolling_average_skips_gaps_without_shifting_the_axis():
    out = research.rolling_average([1.0, None, 3.0, 5.0], 3)
    assert len(out) == 4  # one output point per input point, always
    assert out[3] == 3.0  # mean of 1, 3, 5


def test_monthly_aggregation_averages_temperature():
    times = ["2024-01-01", "2024-01-31", "2024-02-01"]
    labels, values = research._aggregate(times, [10.0, 20.0, 5.0], "monthly", "mean")
    assert labels == ["2024-01", "2024-02"]
    assert values == [15.0, 5.0]


def test_monthly_aggregation_sums_precipitation():
    """Averaging daily rainfall totals would understate a month roughly
    thirtyfold - precipitation must sum, not mean."""
    times = ["2024-06-01", "2024-06-02", "2024-06-03"]
    labels, values = research._aggregate(times, [10.0, 20.0, 30.0], "monthly", "sum")
    assert labels == ["2024-06"]
    assert values == [60.0]


def test_yearly_aggregation_buckets_by_year():
    times = ["2023-05-01", "2024-05-01", "2024-09-01"]
    labels, values = research._aggregate(times, [1.0, 3.0, 5.0], "yearly", "mean")
    assert labels == ["2023", "2024"]
    assert values == [1.0, 4.0]


def test_daily_aggregation_passes_through_untouched():
    times = ["2024-01-01", "2024-01-02"]
    assert research._aggregate(times, [1.0, 2.0], "daily", "mean") == (times, [1.0, 2.0])


def test_aggregation_ignores_missing_observations():
    times = ["2024-01-01", "2024-01-02"]
    labels, values = research._aggregate(times, [10.0, None], "monthly", "mean")
    assert values == [10.0]


# --- model comparison helpers ----------------------------------------


def test_spread_is_the_widest_single_timestep_disagreement():
    series = [
        {"values": [10.0, 12.0, 14.0]},
        {"values": [10.5, 18.0, 14.2]},
    ]
    assert research._spread(series) == 6.0


def test_spread_is_none_for_a_single_model():
    assert research._spread([{"values": [1.0, 2.0]}]) is None


def test_spread_tolerates_missing_values():
    assert research._spread([{"values": [1.0, None]}, {"values": [4.0, None]}]) == 3.0


def test_every_catalog_model_has_a_label():
    for model in research.FORECAST_MODELS:
        assert research._model_label(model["id"]) == model["label"]
    assert research._model_label("not_a_model") == "not_a_model"


def test_default_models_are_all_in_the_catalog():
    ids = {m["id"] for m in research.FORECAST_MODELS}
    assert set(research.DEFAULT_MODELS) <= ids


# --- validation ------------------------------------------------------


def test_unknown_model_is_rejected_before_any_network_call():
    with pytest.raises(research.ResearchError, match="Unknown model"):
        research.compare_models(None, "Bhopal", ["not_a_real_model"])


def test_out_of_range_forecast_days_is_rejected():
    with pytest.raises(research.ResearchError, match="forecast_days"):
        research.compare_models(None, "Bhopal", ["gfs_seamless"], forecast_days=30)


def test_unknown_historical_parameter_is_rejected():
    with pytest.raises(research.ResearchError, match="Unknown parameter"):
        research.historical_trend(None, "Bhopal", parameter="humidity")


def test_bad_aggregation_is_rejected():
    with pytest.raises(research.ResearchError, match="aggregation"):
        research.historical_trend(None, "Bhopal", aggregation="hourly")


def test_reversed_date_range_is_rejected():
    with pytest.raises(research.ResearchError, match="before"):
        research.historical_trend(None, "Bhopal", start_date="2024-01-01", end_date="2020-01-01")


def test_date_before_the_archive_starts_is_rejected():
    with pytest.raises(research.ResearchError, match="archive starts"):
        research.historical_trend(None, "Bhopal", start_date="1900-01-01", end_date="1910-01-01")


def test_malformed_date_is_rejected():
    with pytest.raises(research.ResearchError, match="Invalid date"):
        research.historical_trend(None, "Bhopal", start_date="01-01-2024", end_date="2024-12-31")


# --- regional context ------------------------------------------------


def _point(name, region, pressure=None, gust=None):
    return {"name": name, "region": region, "current": {"pressure_msl": pressure, "wind_gusts_10m": gust}}


def test_lowest_pressure_picks_the_minimum():
    points = [_point("A", "arabian_sea", 1008.0), _point("B", "bay_of_bengal", 1002.5)]
    assert research._lowest_pressure(points) == {"name": "B", "value": 1002.5, "unit": "hPa"}


def test_strongest_gust_picks_the_maximum():
    points = [_point("A", "arabian_sea", gust=8.0), _point("B", "bay_of_bengal", gust=14.2)]
    assert research._strongest_gust(points) == {"name": "B", "value": 14.2, "unit": "m/s"}


def test_regional_summaries_are_none_when_the_field_is_missing():
    points = [_point("A", "arabian_sea")]
    assert research._lowest_pressure(points) is None
    assert research._strongest_gust(points) is None


def test_sampling_points_cover_both_seas_and_both_coasts():
    regions = {p["region"] for p in research.SEA_POINTS} | {p["region"] for p in research.COASTAL_POINTS}
    assert regions == {"arabian_sea", "bay_of_bengal", "west_coast", "east_coast"}
