from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.core.enums import SurfLevel, TideState
from apps.locations.tests.factories import SurfSpotFactory
from apps.surf_conditions.models import SurfCondition, compass_label, score_band

from .factories import ConditionForecastFactory, SurfConditionFactory, SurfScoreFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# SurfCondition
# ---------------------------------------------------------------------------
def test_str_names_the_spot_and_the_moment():
    condition = SurfConditionFactory(spot__name="Test Beach")
    assert "Test Beach" in str(condition)


def test_unit_conversions_are_exact_enough_to_read_out():
    condition = SurfConditionFactory(wave_height_m=1.5, wind_speed_kmh=18.52)
    assert condition.wave_height_ft == pytest.approx(4.9, abs=0.05)
    assert condition.wind_knots == pytest.approx(10.0, abs=0.05)


def test_missing_measurements_stay_none_rather_than_zero():
    condition = SurfConditionFactory(wave_height_m=None, wind_speed_kmh=None)
    assert condition.wave_height_ft is None
    assert condition.wind_knots is None
    assert condition.has_wave_data is False


def test_is_stale_after_three_hours():
    fresh = SurfConditionFactory(recorded_at=timezone.now() - timedelta(minutes=30))
    old = SurfConditionFactory(
        spot=fresh.spot, recorded_at=timezone.now() - timedelta(hours=4)
    )
    assert fresh.is_stale is False
    assert old.is_stale is True


def test_a_forecast_row_is_never_stale():
    forecast = SurfConditionFactory(
        is_forecast=True, recorded_at=timezone.now() - timedelta(hours=9)
    )
    assert forecast.is_stale is False


def test_recommended_wetsuit_follows_the_water_temperature():
    warm = SurfConditionFactory(water_temperature_c=25.0)
    cold = SurfConditionFactory(spot=warm.spot, water_temperature_c=10.0)
    assert "boardshorts" in warm.recommended_wetsuit
    assert "5/4" in cold.recommended_wetsuit


def test_effective_period_prefers_the_swell_period():
    condition = SurfConditionFactory(wave_period_s=6.0, swell_period_s=12.0)
    assert condition.effective_period_s == 12.0
    condition.swell_period_s = None
    assert condition.effective_period_s == 6.0


def test_lightning_is_detected_from_the_weather_code():
    assert SurfConditionFactory(weather_code=95).has_lightning is True
    assert SurfConditionFactory(weather_code=0).has_lightning is False


def test_one_reading_per_spot_time_and_kind():
    spot = SurfSpotFactory()
    moment = timezone.now()
    SurfConditionFactory(spot=spot, recorded_at=moment, is_forecast=False)
    with pytest.raises(IntegrityError):
        SurfCondition.objects.create(spot=spot, recorded_at=moment, is_forecast=False)


def test_a_forecast_and_an_observation_may_describe_the_same_hour():
    spot = SurfSpotFactory()
    moment = timezone.now()
    SurfConditionFactory(spot=spot, recorded_at=moment, is_forecast=False)
    SurfConditionFactory(spot=spot, recorded_at=moment, is_forecast=True)
    assert SurfCondition.objects.filter(spot=spot).count() == 2


# ---------------------------------------------------------------------------
# SurfScore
# ---------------------------------------------------------------------------
def test_score_bands_map_to_colours():
    assert score_band(95) == ("excellent", "emerald")
    assert score_band(65) == ("good", "sky")
    assert score_band(45) == ("fair", "amber")
    assert score_band(10) == ("poor", "rose")
    assert score_band(None) == ("unknown", "slate")


def test_score_str_reads_like_a_score():
    score = SurfScoreFactory(level=SurfLevel.BEGINNER, score=72)
    assert "72/100" in str(score)


def test_a_computed_score_is_never_flagged_as_ai():
    assert SurfScoreFactory().is_ai_generated is False


def test_one_score_per_condition_and_level():
    score = SurfScoreFactory()
    with pytest.raises(IntegrityError):
        SurfScoreFactory(condition=score.condition, level=score.level)


def test_blocking_factors_surface_the_two_worst_components():
    score = SurfScoreFactory(
        factors=[
            {"name": "Wave height", "score": 90},
            {"name": "Wind", "score": 20},
            {"name": "Tide", "score": 40},
            {"name": "Weather", "score": None},
        ]
    )
    names = [factor["name"] for factor in score.blocking_factors]
    assert names == ["Wind", "Tide"]


# ---------------------------------------------------------------------------
# ConditionForecast
# ---------------------------------------------------------------------------
def test_forecast_window_display_handles_a_missing_window():
    forecast = ConditionForecastFactory(best_window_start=None, best_window_end=None)
    assert forecast.has_best_window is False
    assert forecast.best_window_display == "—"


def test_forecast_summary_accessors_tolerate_junk():
    forecast = ConditionForecastFactory(summary={"wave_height_max": "not a number"})
    assert forecast.wave_height_max is None
    assert forecast.hours == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (202.5, "SSW"), (None, "—")],
)
def test_compass_label(degrees, expected):
    assert compass_label(degrees) == expected


def test_tide_state_defaults_to_unknown():
    condition = SurfCondition(spot=SurfSpotFactory(), recorded_at=timezone.now())
    assert condition.tide_state == TideState.UNKNOWN
