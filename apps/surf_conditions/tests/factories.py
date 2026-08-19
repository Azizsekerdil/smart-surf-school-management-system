"""Factories for surf conditions.

The defaults describe a good, safe morning: chest-high clean swell, light
offshore wind, mid-rising tide, comfortable water. Tests that want a bad day
override exactly the field they are testing, which keeps each test's intent
visible in its own body.
"""

from __future__ import annotations

from datetime import timedelta

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.core.enums import SurfLevel, TideState, WindType
from apps.locations.tests.factories import SurfSpotFactory
from apps.surf_conditions.models import ConditionForecast, SurfCondition, SurfScore


class SurfConditionFactory(DjangoModelFactory):
    class Meta:
        model = SurfCondition

    spot = factory.SubFactory(SurfSpotFactory)
    # Windows' clock granularity is ~16 ms, so two ``timezone.now()`` calls can
    # return the identical instant and collide with the (spot, recorded_at,
    # is_forecast) unique constraint. The sequence offset guarantees distinct
    # readings without any test having to think about it.
    recorded_at = factory.Sequence(lambda n: timezone.now() - timedelta(seconds=n))
    is_forecast = False
    source = SurfCondition.Source.PROVIDER
    provider = "open-meteo"

    wave_height_m = 1.0
    wave_period_s = 9.0
    wave_direction_deg = 200.0
    swell_height_m = 0.9
    swell_period_s = 10.0
    swell_direction_deg = 200.0
    wind_wave_height_m = 0.2

    # The factory spot faces 180°, so an offshore wind blows from 0°.
    wind_speed_kmh = 12.0
    wind_gust_kmh = 18.0
    wind_direction_deg = 0.0
    wind_type = WindType.OFFSHORE

    sea_level_height_msl_m = 0.4
    tide_state = TideState.MID_RISING

    air_temperature_c = 24.0
    water_temperature_c = 22.0

    weather_code = 0
    weather_description = "Clear sky"
    uv_index = 6.0
    precipitation_mm = 0.0
    cloud_cover_pct = 5.0
    visibility_km = 24.0
    raw_payload = factory.Dict({"provider": "open-meteo", "test": True})


class ForecastConditionFactory(SurfConditionFactory):
    """A modelled future hour rather than an observation."""

    is_forecast = True


class SurfScoreFactory(DjangoModelFactory):
    class Meta:
        model = SurfScore

    condition = factory.SubFactory(SurfConditionFactory)
    level = SurfLevel.BEGINNER
    score = 75
    factors = factory.List([])
    recommendation = "Good conditions for Beginner students."
    is_safe_for_level = True
    is_ai_generated = False


class ConditionForecastFactory(DjangoModelFactory):
    class Meta:
        model = ConditionForecast

    spot = factory.SubFactory(SurfSpotFactory)
    date = factory.LazyFunction(timezone.localdate)
    summary = factory.Dict({"hours": [], "wave_height_max": 1.2})
    best_level = SurfLevel.BEGINNER
