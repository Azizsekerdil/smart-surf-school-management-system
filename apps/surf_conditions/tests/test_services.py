"""The surf score is a safety-relevant calculation, so it is tested hard.

Every threshold in :mod:`apps.core.enums` that the score depends on is asserted
here, including both sides of the hard safety gate.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.core.enums import MAX_WIND_KMH, WAVE_HEIGHT_SUITABILITY, SurfLevel, TideState, WindType
from apps.locations.tests.factories import SurfSpotFactory
from apps.surf_conditions import services
from apps.surf_conditions.models import SurfCondition, SurfScore
from apps.surf_conditions.providers.base import ConditionSnapshot

from .factories import SurfConditionFactory

pytestmark = pytest.mark.django_db


def factor(result: dict, key: str) -> dict:
    return next(item for item in result["factors"] if item["key"] == key)


# ---------------------------------------------------------------------------
# Wave height
# ---------------------------------------------------------------------------
def test_ideal_wave_height_scores_full_marks_on_the_wave_component():
    _low, ideal_low, ideal_high, _max = WAVE_HEIGHT_SUITABILITY[SurfLevel.BEGINNER]
    height = (ideal_low + ideal_high) / 2
    condition = SurfConditionFactory(wave_height_m=height)
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert factor(result, "wave_height")["score"] == 100


def test_flat_water_scores_badly_without_being_unsafe():
    condition = SurfConditionFactory(wave_height_m=0.05)
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert factor(result, "wave_height")["score"] < 20
    assert result["is_safe"] is True


def test_the_same_reading_scores_differently_for_different_levels():
    condition = SurfConditionFactory(wave_height_m=1.8, wind_speed_kmh=10.0)
    beginner = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    intermediate = services.calculate_surf_score(condition, SurfLevel.INTERMEDIATE)
    assert beginner["is_safe"] is False   # 1.8 m is over the 1.2 m beginner limit
    assert intermediate["is_safe"] is True
    assert intermediate["score"] > beginner["score"]


# ---------------------------------------------------------------------------
# The hard safety gate
# ---------------------------------------------------------------------------
def test_wave_over_the_limit_caps_the_score_and_marks_it_unsafe():
    _low, _il, _ih, max_safe = WAVE_HEIGHT_SUITABILITY[SurfLevel.FIRST_TIME]
    condition = SurfConditionFactory(
        wave_height_m=max_safe + 0.5,
        # Everything else is perfect, so only the gate can produce a low score.
        wind_speed_kmh=6.0,
        swell_period_s=11.0,
        water_temperature_c=24.0,
        tide_state=TideState.MID_RISING,
    )
    result = services.calculate_surf_score(condition, SurfLevel.FIRST_TIME)
    assert result["is_safe"] is False
    assert result["score"] <= services.UNSAFE_SCORE_CAP
    assert result["gates"]


def test_wind_over_the_limit_caps_the_score():
    condition = SurfConditionFactory(
        wave_height_m=0.5,
        wind_speed_kmh=MAX_WIND_KMH[SurfLevel.BEGINNER] + 5,
        wind_gust_kmh=MAX_WIND_KMH[SurfLevel.BEGINNER] + 15,
    )
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert result["is_safe"] is False
    assert result["score"] <= services.UNSAFE_SCORE_CAP


def test_lightning_closes_the_water_at_every_level():
    condition = SurfConditionFactory(wave_height_m=0.6, wind_speed_kmh=5.0, weather_code=95)
    for level, _label in SurfLevel.choices:
        result = services.calculate_surf_score(condition, level)
        assert result["is_safe"] is False
        assert result["score"] <= services.UNSAFE_SCORE_CAP


def test_a_reading_just_under_the_limit_is_still_safe():
    _low, _il, _ih, max_safe = WAVE_HEIGHT_SUITABILITY[SurfLevel.BEGINNER]
    condition = SurfConditionFactory(wave_height_m=max_safe - 0.01, wind_speed_kmh=8.0)
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert result["is_safe"] is True


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------
def test_offshore_wind_beats_onshore_wind_at_the_same_speed():
    spot = SurfSpotFactory(beach_facing_deg=180.0)
    offshore = SurfConditionFactory(spot=spot, wind_direction_deg=0.0, wind_speed_kmh=15.0)
    onshore = SurfConditionFactory(
        spot=spot,
        wind_direction_deg=180.0,
        wind_speed_kmh=15.0,
        recorded_at=timezone.now() - timedelta(minutes=5),
    )
    offshore_score = services.calculate_surf_score(offshore, SurfLevel.INTERMEDIATE)
    onshore_score = services.calculate_surf_score(onshore, SurfLevel.INTERMEDIATE)
    assert factor(offshore_score, "wind")["score"] > factor(onshore_score, "wind")["score"]


def test_near_zero_wind_is_classified_as_glassy():
    condition = SurfConditionFactory(wind_speed_kmh=2.0, wind_direction_deg=180.0)
    assert services.classify_wind(condition) == WindType.GLASSY


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------
def test_no_wave_height_means_no_score_at_all():
    condition = SurfConditionFactory(wave_height_m=None, swell_height_m=None)
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert result["has_data"] is False
    assert result["is_safe"] is False
    assert result["score"] == 0


def test_missing_components_are_excluded_not_guessed():
    condition = SurfConditionFactory(
        water_temperature_c=None, tide_state=TideState.UNKNOWN, swell_period_s=None,
        wave_period_s=None,
    )
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert factor(result, "water_temperature")["measured"] is False
    assert factor(result, "tide")["measured"] is False
    assert factor(result, "period")["measured"] is False
    # A perfect wave with three unknown components still scores well, because
    # the unknowns are dropped rather than counted as zero.
    assert result["score"] > 60


def test_score_never_leaves_the_zero_to_hundred_range():
    for height in (0.0, 0.3, 1.0, 3.0, 12.0):
        condition = SurfConditionFactory(wave_height_m=height)
        for level, _label in SurfLevel.choices:
            result = services.calculate_surf_score(condition, level)
            assert 0 <= result["score"] <= 100


def test_factors_carry_everything_the_ui_needs_to_explain_the_number():
    condition = SurfConditionFactory()
    result = services.calculate_surf_score(condition, SurfLevel.BEGINNER)
    assert len(result["factors"]) == 6
    for item in result["factors"]:
        assert set(item) >= {"name", "value", "score", "weight", "note"}
        assert isinstance(item["name"], str)
        assert isinstance(item["note"], str)


def test_the_declared_weights_sum_to_one():
    assert sum(services.SCORE_WEIGHTS.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tide
# ---------------------------------------------------------------------------
def test_the_ideal_tide_scores_higher_than_the_opposite_one():
    spot = SurfSpotFactory(ideal_tide=TideState.HIGH)
    good = SurfConditionFactory(spot=spot, tide_state=TideState.HIGH)
    bad = SurfConditionFactory(
        spot=spot, tide_state=TideState.LOW, recorded_at=timezone.now() - timedelta(minutes=5)
    )
    assert factor(services.calculate_surf_score(good, SurfLevel.BEGINNER), "tide")["score"] == 100
    assert factor(services.calculate_surf_score(bad, SurfLevel.BEGINNER), "tide")["score"] < 50


# ---------------------------------------------------------------------------
# Storing and scoring
# ---------------------------------------------------------------------------
def test_score_condition_stores_one_row_per_level():
    condition = SurfConditionFactory()
    stored = services.score_condition(condition)
    assert len(stored) == len(SurfLevel.choices)
    assert SurfScore.objects.filter(condition=condition).count() == len(SurfLevel.choices)
    assert all(score.is_ai_generated is False for score in stored)


def test_score_condition_stores_nothing_without_wave_data():
    condition = SurfConditionFactory(wave_height_m=None)
    assert services.score_condition(condition) == []
    assert SurfScore.objects.filter(condition=condition).count() == 0


def test_scoring_is_idempotent():
    condition = SurfConditionFactory()
    services.score_condition(condition)
    services.score_condition(condition)
    assert SurfScore.objects.filter(condition=condition).count() == len(SurfLevel.choices)


def test_store_snapshot_is_idempotent_and_classifies_the_wind():
    spot = SurfSpotFactory(beach_facing_deg=180.0)
    moment = timezone.now().replace(microsecond=0)
    snapshot = ConditionSnapshot(
        recorded_at=moment,
        wave_height_m=1.0,
        wind_speed_kmh=20.0,
        wind_direction_deg=0.0,
        tide_state=TideState.HIGH,
        raw={"provider": "test"},
    )
    first = services.store_snapshot(spot, snapshot, provider_name="test", is_forecast=False)
    second = services.store_snapshot(spot, snapshot, provider_name="test", is_forecast=False)
    assert first.pk == second.pk
    assert first.wind_type == WindType.OFFSHORE
    assert SurfCondition.objects.filter(spot=spot).count() == 1


# ---------------------------------------------------------------------------
# Refreshing — the provider is always mocked, no test touches the network
# ---------------------------------------------------------------------------
class _StubProvider:
    name = "stub"
    label = "Stub"
    attribution = "Stub data"
    provides_marine_data = True

    def __init__(self, current=None, forecast=None):
        self._current = current
        self._forecast = forecast or []

    def fetch_current(self, spot):
        return self._current

    def fetch_forecast(self, spot, days=7):
        return self._forecast

    def health_check(self):
        return True, "ok"


def _snapshot(offset_hours: int = 0, **overrides) -> ConditionSnapshot:
    values = {
        "recorded_at": (timezone.now() + timedelta(hours=offset_hours)).replace(
            minute=0, second=0, microsecond=0
        ),
        "wave_height_m": 1.0,
        "swell_period_s": 10.0,
        "wind_speed_kmh": 12.0,
        "wind_direction_deg": 0.0,
        "water_temperature_c": 21.0,
        "tide_state": TideState.MID_RISING,
        "weather_code": 0,
    }
    values.update(overrides)
    return ConditionSnapshot(**values)


def test_refresh_stores_and_scores_the_current_reading():
    spot = SurfSpotFactory()
    provider = _StubProvider(current=_snapshot())
    condition = services.refresh_spot_conditions(spot, include_forecast=False, provider=provider)
    assert condition is not None
    assert condition.provider == "stub"
    assert SurfScore.objects.filter(condition=condition).count() == len(SurfLevel.choices)


def test_refresh_returns_none_when_the_provider_has_nothing():
    spot = SurfSpotFactory()
    provider = _StubProvider(current=None)
    assert services.refresh_spot_conditions(spot, include_forecast=False, provider=provider) is None


def test_refresh_never_raises_when_the_provider_explodes():
    class _Broken(_StubProvider):
        def fetch_current(self, spot):
            raise RuntimeError("upstream on fire")

    spot = SurfSpotFactory()
    assert services.refresh_spot_conditions(spot, provider=_Broken()) is None


def test_forecast_refresh_stores_hours_and_builds_daily_rollups():
    spot = SurfSpotFactory()
    forecast = [_snapshot(offset_hours=hour) for hour in range(0, 24)]
    provider = _StubProvider(current=_snapshot(), forecast=forecast)

    stored = services.refresh_spot_forecast(spot, days=2, provider=provider)
    assert stored == 24
    assert SurfCondition.objects.filter(spot=spot, is_forecast=True).count() == 24

    rollups = services.upcoming_forecasts(spot)
    assert rollups
    assert rollups[0].summary["hour_count"] > 0


def test_forecast_refresh_is_idempotent():
    spot = SurfSpotFactory()
    forecast = [_snapshot(offset_hours=hour) for hour in range(0, 6)]
    provider = _StubProvider(forecast=forecast)
    services.refresh_spot_forecast(spot, provider=provider)
    services.refresh_spot_forecast(spot, provider=provider)
    assert SurfCondition.objects.filter(spot=spot, is_forecast=True).count() == 6


# ---------------------------------------------------------------------------
# Best window
# ---------------------------------------------------------------------------
def test_best_window_picks_the_calmest_stretch():
    spot = SurfSpotFactory()
    today = timezone.localdate()
    base = timezone.make_aware(
        datetime.combine(today, time.min), timezone.get_current_timezone()
    )
    # 08:00-11:00 is clean; the afternoon is blown out.
    for hour in range(6, 19):
        SurfConditionFactory(
            spot=spot,
            recorded_at=base + timedelta(hours=hour),
            is_forecast=True,
            wave_height_m=0.7,
            wind_speed_kmh=8.0 if 8 <= hour <= 11 else 28.0,
            wind_direction_deg=0.0,
        )
    window = services.best_time_window(spot, today, SurfLevel.BEGINNER)
    assert window is not None
    assert window["start"].hour == 8
    assert window["end"].hour == 12


def test_best_window_is_none_when_nothing_is_stored():
    spot = SurfSpotFactory()
    assert services.best_time_window(spot, timezone.localdate(), SurfLevel.BEGINNER) is None


# ---------------------------------------------------------------------------
# The AI tool contract
# ---------------------------------------------------------------------------
def test_tool_returns_the_no_data_shape_when_no_spot_exists():
    payload = services.conditions_for_tool()
    assert payload["status"] == services.NO_DATA_STATUS
    assert payload["count"] == 0
    assert payload["results"] == []
    assert payload["message"]


def test_tool_returns_the_no_data_shape_for_an_unknown_spot():
    SurfSpotFactory(is_primary=True)
    payload = services.conditions_for_tool(spot_query="Nowhere Beach")
    assert payload["status"] == services.NO_DATA_STATUS


def test_tool_says_so_when_a_spot_has_no_readings():
    SurfSpotFactory(name="Empty Bay", is_active=True)
    payload = services.conditions_for_tool(spot_query="Empty Bay")
    assert payload["status"] == services.NO_DATA_STATUS
    assert payload["count"] == 0


def test_tool_returns_real_values_and_never_invents_them():
    spot = SurfSpotFactory(name="Tool Beach")
    condition = SurfConditionFactory(spot=spot, wave_height_m=1.1, wind_speed_kmh=14.0)
    services.score_condition(condition)

    payload = services.conditions_for_tool(spot_query="Tool Beach")
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["conditions"]["wave_height_m"] == 1.1
    assert payload["spot"]["name"] == "Tool Beach"
    assert payload["scores_by_level"]
    assert "not generated by a language model" in payload["note"]


def test_tool_omits_scores_when_there_is_no_wave_data():
    spot = SurfSpotFactory(name="Windy Point")
    SurfConditionFactory(spot=spot, wave_height_m=None, wind_speed_kmh=20.0)
    payload = services.conditions_for_tool(spot_query="Windy Point")
    assert payload["status"] == "ok"
    assert payload["scores_by_level"] == []
    assert payload["scores_unavailable_reason"]


# ---------------------------------------------------------------------------
# Dashboard payload
# ---------------------------------------------------------------------------
def test_dashboard_payload_survives_a_spot_with_no_data():
    spot = SurfSpotFactory()
    payload = services.dashboard_payload(spot)
    assert payload["condition"] is None
    assert payload["scores"] == []
    assert payload["attribution"]


def test_current_or_nearest_falls_back_to_a_forecast_hour():
    spot = SurfSpotFactory()
    SurfConditionFactory(spot=spot, recorded_at=timezone.now() - timedelta(hours=8))
    upcoming = SurfConditionFactory(
        spot=spot, is_forecast=True, recorded_at=timezone.now() + timedelta(minutes=30)
    )
    assert services.current_or_nearest(spot).pk == upcoming.pk
