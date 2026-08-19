"""Unit tests for the statistics engine.

Every expected value here is computed by hand or from a textbook example, not
from the implementation. This is the part of analytics that a school makes
decisions from, so "it returns something plausible" is not a passing grade.

No database, no Django models, no network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.analytics import statistics as stats

# Classic worked example: mean 5, population variance 4, population sd 2.
TEXTBOOK = [2, 4, 4, 4, 5, 5, 7, 9]


# ---------------------------------------------------------------------------
# Centre
# ---------------------------------------------------------------------------
def test_mean_of_known_series():
    assert stats.mean([1, 2, 3, 4]) == 2.5


def test_mean_ignores_holes_and_junk():
    assert stats.mean([1, None, 3, "not a number"]) == 2.0


def test_mean_accepts_decimals_because_money_is_decimal():
    assert stats.mean([Decimal("10.50"), Decimal("11.50")]) == 11.0


def test_mean_of_nothing_is_none():
    assert stats.mean([]) is None
    assert stats.mean(None) is None


def test_median_even_and_odd():
    assert stats.median([1, 2, 3, 4]) == 2.5
    assert stats.median([1, 2, 3]) == 2.0


def test_median_is_unmoved_by_one_freak_value():
    assert stats.median([1, 2, 3, 4, 10_000]) == 3.0


def test_mode_returns_the_most_common_value():
    assert stats.mode([1, 2, 2, 3]) == 2.0


def test_mode_breaks_ties_deterministically_on_the_lowest():
    assert stats.mode([1, 1, 2, 2]) == 1.0


def test_mode_of_nothing_is_none():
    assert stats.mode([]) is None


# ---------------------------------------------------------------------------
# Spread
# ---------------------------------------------------------------------------
def test_population_standard_deviation_matches_the_textbook():
    assert stats.std_dev(TEXTBOOK, sample=False) == pytest.approx(2.0)


def test_sample_standard_deviation_uses_bessels_correction():
    # sum of squared deviations = 32, n - 1 = 7
    assert stats.std_dev(TEXTBOOK, sample=True) == pytest.approx((32 / 7) ** 0.5)


def test_sample_standard_deviation_needs_two_points():
    assert stats.std_dev([5]) is None
    assert stats.variance([5]) is None


def test_population_standard_deviation_of_one_point_is_zero():
    assert stats.std_dev([5], sample=False) == 0.0


def test_zero_variance_is_zero_not_none():
    assert stats.variance([3, 3, 3]) == 0.0
    assert stats.std_dev([3, 3, 3]) == 0.0


def test_percentiles_interpolate_linearly():
    assert stats.percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert stats.percentile([1, 2, 3, 4, 5], 25) == 2.0
    assert stats.percentile([1, 2, 3, 4, 5], 75) == 4.0


def test_percentile_clamps_out_of_range_input():
    assert stats.percentile([1, 2, 3], 500) == 3.0
    assert stats.percentile([1, 2, 3], -10) == 1.0


def test_percentile_of_nothing_is_none():
    assert stats.percentile([], 50) is None
    assert stats.percentile([1, 2], "not a percentile") is None


def test_quartiles_and_iqr():
    assert stats.quartiles([1, 2, 3, 4, 5]) == {"q1": 2.0, "q2": 3.0, "q3": 4.0}
    assert stats.iqr([1, 2, 3, 4, 5]) == 2.0
    assert stats.quartiles([]) is None
    assert stats.iqr([]) is None


def test_percent_change_matches_the_shared_helper():
    assert stats.percent_change(110, 100) == 10.0
    assert stats.percent_change(90, 100) == -10.0
    assert stats.percent_change(0, 0) is None


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------
def test_moving_average_is_aligned_to_the_input():
    assert stats.moving_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_moving_average_window_of_one_is_the_series_itself():
    assert stats.moving_average([1, 2], 1) == [1.0, 2.0]


def test_moving_average_refuses_an_impossible_window():
    assert stats.moving_average([1, 2], 5) == [None, None]
    assert stats.moving_average([1, 2], "three") == [None, None]


def test_moving_average_will_not_average_a_partial_window():
    # Position 2 covers a hole, so it reports nothing rather than half an answer.
    assert stats.moving_average([1, None, 3, 4], 2) == [None, None, None, 3.5]


def test_weighted_moving_average_weights_the_newest_point_last():
    result = stats.weighted_moving_average([1, 2, 3, 4], [1, 2, 3])
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(14 / 6)
    assert result[3] == pytest.approx(20 / 6)


def test_weighted_moving_average_rejects_useless_weights():
    assert stats.weighted_moving_average([1, 2, 3], []) == [None, None, None]
    assert stats.weighted_moving_average([1, 2, 3], [0, 0]) == [None, None, None]
    assert stats.weighted_moving_average([1, 2, 3], None) == [None, None, None]


def test_exponential_smoothing_starts_at_the_first_point():
    assert stats.exponential_smoothing([10, 20, 30], 0.5) == [10.0, 15.0, 22.5]


def test_exponential_smoothing_falls_back_on_an_invalid_alpha():
    # alpha=5 is nonsense, so 0.3 is used: 10, then 0.3*20 + 0.7*10 = 13.
    assert stats.exponential_smoothing([10, 20], 5) == [10.0, 13.0]


def test_exponential_smoothing_of_nothing_is_empty():
    assert stats.exponential_smoothing([]) == []


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def test_linear_regression_on_a_perfect_line():
    slope, intercept, r_squared = stats.linear_regression([1, 2, 3, 4], [2, 4, 6, 8])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(0.0)
    assert r_squared == pytest.approx(1.0)


def test_linear_regression_is_undefined_without_two_points():
    assert stats.linear_regression([1], [2]) == (None, None, None)
    assert stats.linear_regression([], []) == (None, None, None)


def test_linear_regression_refuses_a_vertical_line():
    assert stats.linear_regression([1, 1, 1], [2, 3, 4]) == (None, None, None)


def test_linear_regression_describes_flat_data_perfectly():
    slope, _intercept, r_squared = stats.linear_regression([1, 2, 3], [5, 5, 5])
    assert slope == pytest.approx(0.0)
    assert r_squared == pytest.approx(1.0)


def test_trend_detects_a_clean_rise():
    result = stats.trend([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert result["direction"] == "up"
    assert result["slope"] == pytest.approx(1.0)
    assert result["change"] == pytest.approx(9.0)
    assert result["r_squared"] == pytest.approx(1.0)
    assert result["confidence"] == "high"


def test_trend_detects_a_fall():
    assert stats.trend([10, 9, 8, 7, 6, 5, 4, 3])["direction"] == "down"


def test_trend_calls_a_constant_series_flat():
    assert stats.trend([100, 100, 100, 100, 100])["direction"] == "flat"


def test_trend_will_not_claim_confidence_from_a_short_series():
    result = stats.trend([1, 2, 3])
    assert result["r_squared"] == pytest.approx(1.0)
    assert result["confidence"] == "low"


def test_trend_survives_an_empty_series():
    result = stats.trend([])
    assert result["n"] == 0
    assert result["direction"] == "flat"
    assert result["slope"] is None


def test_correlation_of_a_perfect_positive_pair():
    result = stats.correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    assert result["r"] == pytest.approx(1.0)
    assert result["strength"] == "very_strong"
    assert result["direction"] == "positive"
    assert str(result["label"])


def test_correlation_of_a_perfect_inverse_pair():
    result = stats.correlation([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])
    assert result["r"] == pytest.approx(-1.0)
    assert result["direction"] == "negative"


def test_correlation_refuses_two_points():
    result = stats.correlation([1, 2], [1, 2])
    assert result["r"] is None
    assert result["strength"] == "insufficient_data"


def test_correlation_is_undefined_when_one_series_never_moves():
    assert stats.correlation([1, 1, 1, 1], [1, 2, 3, 4])["strength"] == "undefined"


def test_seasonality_returns_one_factor_per_phase():
    # Phases average 10 and 20 against an overall mean of 15.
    result = stats.seasonality([10, 20, 10, 20, 10, 20], 2)
    assert result == pytest.approx([10 / 15, 20 / 15])


def test_seasonality_needs_a_full_cycle_and_a_non_zero_mean():
    assert stats.seasonality([1, 2], 7) is None
    assert stats.seasonality([1, 2, 3], 0) is None
    assert stats.seasonality([0, 0, 0, 0], 2) is None
    assert stats.seasonality(None, 7) is None


# ---------------------------------------------------------------------------
# Forecasting — the reliability rules matter more than the numbers
# ---------------------------------------------------------------------------
def test_forecast_extrapolates_a_clean_line():
    result = stats.forecast([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], periods=3)
    assert result["values"] == pytest.approx([11.0, 12.0, 13.0])
    assert result["method"] == "linear"
    assert result["r_squared"] == pytest.approx(1.0)
    assert result["warning"] is None


def test_forecast_reaches_high_confidence_only_with_four_horizons_of_history():
    ten_points = list(range(1, 11))
    twelve_points = list(range(1, 13))
    # 10 points, horizon 3: past the 2x floor, short of the 4x bar.
    assert stats.forecast(ten_points, periods=3)["confidence"] == "medium"
    assert stats.forecast(twelve_points, periods=3)["confidence"] == "high"


def test_forecast_warns_and_drops_confidence_on_thin_history():
    result = stats.forecast([1, 2, 3], periods=7)
    assert result["confidence"] == "low"
    assert result["low_confidence"] is True
    assert result["warning_code"] == "insufficient_history"
    assert result["required_points"] == 14
    assert len(result["values"]) == 7


def test_forecast_warns_when_the_trend_explains_nothing():
    noisy = [10, 2, 9, 3, 8, 4, 7, 5, 6, 6, 5, 7, 4, 8, 3, 9, 2, 10, 1, 11]
    result = stats.forecast(noisy, periods=3)
    assert result["confidence"] == "low"
    assert any(item["code"] == "weak_fit" for item in result["warnings"])


def test_forecast_of_nothing_projects_nothing():
    result = stats.forecast([], periods=5)
    assert result["values"] == []
    assert result["method"] == "none"
    assert result["confidence"] == "none"
    assert result["warning_code"] == "no_data"


def test_forecast_of_a_single_point_repeats_it_and_says_so():
    result = stats.forecast([7], periods=4)
    assert result["values"] == [7.0, 7.0, 7.0, 7.0]
    assert result["method"] == "naive"
    assert result["warning_code"] == "single_point"
    assert result["confidence"] == "low"


def test_forecast_never_projects_negative_revenue():
    falling = [10, 8, 6, 4, 2, 1, 0, 0, 0, 0, 0, 0]
    result = stats.forecast(falling, periods=6)
    assert min(result["values"]) >= 0


def test_forecast_keeps_the_sign_of_a_genuinely_signed_series():
    signed = [5, 3, 1, -1, -3, -5, -7, -9, -11, -13, -15, -17]
    result = stats.forecast(signed, periods=3)
    assert min(result["values"]) < 0


def test_forecast_flat_methods_say_they_assume_no_change():
    result = stats.forecast([5] * 8, periods=2, method="mean")
    assert result["values"] == [5.0, 5.0]
    assert result["warning_code"] == "flat_projection"


def test_forecast_falls_back_to_linear_for_an_unknown_method():
    result = stats.forecast(list(range(1, 13)), periods=3, method="crystal-ball")
    assert result["method"] == "linear"


def test_forecast_horizon_is_bounded():
    result = stats.forecast(list(range(50)), periods=10_000)
    assert len(result["values"]) == stats.MAX_FORECAST_PERIODS


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------
def test_iqr_outlier_is_found_by_index():
    assert stats.outliers([1, 2, 3, 4, 100]) == [4]


def test_no_outliers_in_an_even_series():
    assert stats.outliers([1, 2, 3, 4, 5]) == []


def test_outliers_need_four_points():
    assert stats.outliers([1, 100]) == []


def test_a_constant_series_has_no_outliers():
    assert stats.outliers([5, 5, 5, 5, 5]) == []


def test_zscore_method_finds_the_extreme_value():
    series = [1] * 19 + [40]
    assert stats.outliers(series, "zscore") == [19]


def test_outlier_indexes_refer_to_the_original_series_including_holes():
    assert stats.outliers([1, None, 2, 3, 4, 100]) == [5]


def test_unknown_outlier_method_falls_back_to_iqr():
    assert stats.outliers([1, 2, 3, 4, 100], "vibes") == [4]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def test_summarise_reports_every_applicable_statistic():
    result = stats.summarise([1, 2, 3, 4, 5])
    assert result["n"] == 5
    assert result["sum"] == 15.0
    assert result["mean"] == 3.0
    assert result["median"] == 3.0
    assert result["minimum"] == 1.0
    assert result["maximum"] == 5.0
    assert result["range"] == 4.0
    assert result["change_pct"] == 400.0
    assert result["trend"]["direction"] == "up"
    assert result["outliers"] == []
    assert result["coefficient_of_variation"] == pytest.approx(
        stats.std_dev([1, 2, 3, 4, 5]) / 3.0
    )


def test_summarise_of_an_empty_series_keeps_the_full_shape():
    result = stats.summarise([])
    assert result["n"] == 0
    assert result["sum"] == 0.0
    assert result["mean"] is None
    assert result["std_dev"] is None
    assert result["change_pct"] is None
    assert result["outliers"] == []
    assert result["trend"]["confidence"] == "low"


# ---------------------------------------------------------------------------
# Nothing may raise
# ---------------------------------------------------------------------------
JUNK = [None, [], "text", 0, [None, None], ["a", "b"], [float("nan"), float("inf")]]


@pytest.mark.parametrize("values", JUNK)
def test_single_argument_functions_never_raise(values):
    for function in (
        stats.mean,
        stats.median,
        stats.mode,
        stats.std_dev,
        stats.variance,
        stats.iqr,
        stats.quartiles,
        stats.trend,
        stats.outliers,
        stats.summarise,
        stats.exponential_smoothing,
        stats.forecast,
    ):
        function(values)


@pytest.mark.parametrize("values", JUNK)
def test_two_argument_functions_never_raise(values):
    stats.percentile(values, 50)
    stats.moving_average(values, 3)
    stats.weighted_moving_average(values, [1, 2])
    stats.linear_regression(values, values)
    stats.correlation(values, values)
    stats.seasonality(values, 7)
