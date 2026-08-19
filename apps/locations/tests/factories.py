"""Factories for surf spots and hazards.

Other modules build their fixtures on top of :class:`SurfSpotFactory`, so keep
its defaults realistic: an active, patrolled beach break that suits everyone.
"""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.core.enums import (
    BottomType,
    BreakType,
    Severity,
    SurfLevel,
    TideState,
    WindType,
)
from apps.locations.models import SpotHazard, SurfSpot


class SurfSpotFactory(DjangoModelFactory):
    class Meta:
        model = SurfSpot
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Test Break {n}")
    description = "A sandy beach break used by the school for lessons."
    latitude = 36.8000
    longitude = 30.6000
    beach_facing_deg = 180.0
    break_type = BreakType.BEACH_BREAK
    bottom_type = BottomType.SAND
    min_level = SurfLevel.FIRST_TIME
    max_level = SurfLevel.ADVANCED
    ideal_tide = TideState.MID_RISING
    ideal_wind = WindType.OFFSHORE
    ideal_swell_direction_deg = 200.0
    capacity = 20
    is_active = True
    is_primary = False
    lifeguard_on_duty = True
    nearest_hospital = "Coastal State Hospital"
    nearest_hospital_phone = "+90 555 123 45 67"


class SpotHazardFactory(DjangoModelFactory):
    class Meta:
        model = SpotHazard

    spot = factory.SubFactory(SurfSpotFactory)
    name = factory.Sequence(lambda n: f"Hazard {n}")
    severity = Severity.MEDIUM
    description = "Recorded during the seasonal risk assessment."
    is_active = True
    applies_from_tide = ""
    applies_to_tide = ""
