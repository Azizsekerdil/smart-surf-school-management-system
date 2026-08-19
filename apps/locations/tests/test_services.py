from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.core.enums import Severity, SurfLevel, TideState, WindType
from apps.locations import services
from apps.locations.models import SurfSpot

from .factories import SpotHazardFactory, SurfSpotFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def test_get_primary_spot_returns_the_flagged_spot():
    first = SurfSpotFactory(name="Alpha")
    SurfSpotFactory(name="Bravo")
    assert services.get_primary_spot() == first


def test_get_primary_spot_falls_back_to_an_active_spot():
    spot = SurfSpotFactory(name="Alpha")
    SurfSpot.all_objects.filter(pk=spot.pk).update(is_primary=False)
    assert services.get_primary_spot() == spot


def test_get_primary_spot_is_none_without_any_spot():
    assert services.get_primary_spot() is None


def test_spots_suitable_for_level_filters_both_bounds():
    beginner_only = SurfSpotFactory(
        name="Nursery", min_level=SurfLevel.FIRST_TIME, max_level=SurfLevel.BEGINNER
    )
    advanced_only = SurfSpotFactory(
        name="Slab", min_level=SurfLevel.ADVANCED, max_level=SurfLevel.COMPETITION
    )

    beginner_spots = list(services.spots_suitable_for_level(SurfLevel.FIRST_TIME))
    assert beginner_only in beginner_spots
    assert advanced_only not in beginner_spots

    advanced_spots = list(services.spots_suitable_for_level(SurfLevel.ADVANCED))
    assert advanced_only in advanced_spots
    assert beginner_only not in advanced_spots


def test_spots_suitable_for_level_skips_archived_spots():
    spot = SurfSpotFactory(name="Alpha")
    SurfSpotFactory(name="Bravo")
    spot.is_active = False
    SurfSpot.all_objects.filter(pk=spot.pk).update(is_active=False, is_primary=False)
    assert spot not in services.spots_suitable_for_level(SurfLevel.BEGINNER)
    assert spot in services.spots_suitable_for_level(SurfLevel.BEGINNER, include_inactive=True)


def test_classify_wind_for_spot():
    spot = SurfSpotFactory(beach_facing_deg=270.0)  # faces west
    assert services.classify_wind_for_spot(spot, 90.0) == WindType.OFFSHORE
    assert services.classify_wind_for_spot(spot, 270.0) == WindType.ONSHORE
    assert services.wind_is_clean_for_spot(spot, 90.0) is True
    assert services.wind_is_clean_for_spot(spot, 270.0) is False


# ---------------------------------------------------------------------------
# Hazards
# ---------------------------------------------------------------------------
def test_active_hazards_are_sorted_by_severity():
    spot = SurfSpotFactory()
    SpotHazardFactory(spot=spot, name="Litter", severity=Severity.LOW)
    SpotHazardFactory(spot=spot, name="Reef shelf", severity=Severity.CRITICAL)
    SpotHazardFactory(spot=spot, name="Boat lane", severity=Severity.HIGH)
    SpotHazardFactory(spot=spot, name="Old news", severity=Severity.HIGH, is_active=False)

    names = [hazard.name for hazard in services.active_hazards(spot)]
    assert names == ["Reef shelf", "Boat lane", "Litter"]


def test_hazards_are_filtered_by_the_tide_window():
    spot = SurfSpotFactory()
    SpotHazardFactory(
        spot=spot,
        name="Exposed rocks",
        severity=Severity.HIGH,
        applies_from_tide=TideState.LOW,
        applies_to_tide=TideState.LOW,
    )
    assert services.active_hazards(spot, tide_state=TideState.LOW)
    assert not services.active_hazards(spot, tide_state=TideState.HIGH)


def test_blocking_hazards_only_returns_critical():
    spot = SurfSpotFactory()
    SpotHazardFactory(spot=spot, severity=Severity.HIGH)
    critical = SpotHazardFactory(spot=spot, severity=Severity.CRITICAL)
    assert services.blocking_hazards(spot) == [critical]


# ---------------------------------------------------------------------------
# Capacity and ratios
# ---------------------------------------------------------------------------
def test_remaining_capacity_never_goes_negative():
    spot = SurfSpotFactory(capacity=10)
    assert services.remaining_capacity(spot, 4) == 6
    assert services.remaining_capacity(spot, 25) == 0


def test_max_group_size_takes_the_lower_of_capacity_and_ratio():
    small = SurfSpotFactory(name="Pocket", capacity=4)
    big = SurfSpotFactory(name="Wide", capacity=50)
    assert services.max_group_size(small, SurfLevel.BEGINNER) == 4
    assert services.max_group_size(big, SurfLevel.BEGINNER) == 8
    assert services.max_group_size(big, SurfLevel.BEGINNER, has_minors=True) == 6


# ---------------------------------------------------------------------------
# The go / no-go gate
# ---------------------------------------------------------------------------
def test_assessment_is_go_for_a_clean_spot():
    spot = SurfSpotFactory(capacity=20)
    result = services.assess_spot_for_group(spot, level=SurfLevel.BEGINNER, group_size=6)
    assert result.verdict == services.VERDICT_GO
    assert result.blocking == []


def test_assessment_blocks_a_group_below_the_spot_minimum():
    spot = SurfSpotFactory(min_level=SurfLevel.INTERMEDIATE, max_level=SurfLevel.COMPETITION)
    result = services.assess_spot_for_group(spot, level=SurfLevel.FIRST_TIME, group_size=2)
    assert result.verdict == services.VERDICT_NO_GO
    assert any("below that level" in reason for reason in result.blocking)


def test_assessment_blocks_overbooking_the_water():
    spot = SurfSpotFactory(capacity=10)
    result = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=4, occupied_students=8
    )
    assert result.verdict == services.VERDICT_NO_GO
    assert result.remaining_capacity == 2


def test_assessment_blocks_on_a_critical_hazard():
    spot = SurfSpotFactory()
    SpotHazardFactory(spot=spot, name="Sewage outflow", severity=Severity.CRITICAL)
    result = services.assess_spot_for_group(spot, level=SurfLevel.BEGINNER, group_size=2)
    assert result.verdict == services.VERDICT_NO_GO


def test_critical_hazard_outside_its_tide_window_does_not_block():
    spot = SurfSpotFactory()
    SpotHazardFactory(
        spot=spot,
        severity=Severity.CRITICAL,
        applies_from_tide=TideState.LOW,
        applies_to_tide=TideState.LOW,
    )
    blocked = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=2, tide_state=TideState.LOW
    )
    clear = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=2, tide_state=TideState.HIGH
    )
    assert blocked.verdict == services.VERDICT_NO_GO
    assert clear.verdict != services.VERDICT_NO_GO


def test_minors_at_an_unpatrolled_spot_with_a_high_hazard_are_blocked():
    spot = SurfSpotFactory(lifeguard_on_duty=False)
    SpotHazardFactory(spot=spot, name="Lateral rip", severity=Severity.HIGH)
    result = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=4, has_minors=True
    )
    assert result.verdict == services.VERDICT_NO_GO


def test_oversized_minor_group_is_blocked_but_adults_only_warn():
    spot = SurfSpotFactory(capacity=30)
    minors = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=9, has_minors=True
    )
    adults = services.assess_spot_for_group(
        spot, level=SurfLevel.BEGINNER, group_size=9
    )
    assert minors.verdict == services.VERDICT_NO_GO
    assert adults.verdict == services.VERDICT_CAUTION


def test_offshore_wind_warns_for_beginners():
    spot = SurfSpotFactory(beach_facing_deg=180.0)
    result = services.assess_spot_for_group(
        spot, level=SurfLevel.FIRST_TIME, group_size=3, wind_direction_deg=0.0
    )
    assert result.wind_type == WindType.OFFSHORE
    assert result.verdict == services.VERDICT_CAUTION
    assert any("Offshore wind" in warning for warning in result.warnings)


def test_missing_emergency_contact_is_a_warning():
    spot = SurfSpotFactory(nearest_hospital="", nearest_hospital_phone="")
    result = services.assess_spot_for_group(spot, level=SurfLevel.BEGINNER, group_size=2)
    assert result.verdict == services.VERDICT_CAUTION


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def test_set_primary_spot_moves_the_flag():
    first = SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    services.set_primary_spot(second)
    first.refresh_from_db()
    second.refresh_from_db()
    assert second.is_primary is True
    assert first.is_primary is False


def test_set_primary_spot_refuses_an_archived_spot():
    SurfSpotFactory(name="Alpha")
    inactive = SurfSpotFactory(name="Bravo", is_active=False)
    with pytest.raises(ValidationError):
        services.set_primary_spot(inactive)


def test_cannot_archive_the_last_active_spot():
    spot = SurfSpotFactory()
    allowed, reason = services.can_archive_spot(spot)
    assert allowed is False
    assert reason
    with pytest.raises(ValidationError):
        services.archive_spot(spot)


def test_archive_spot_soft_deletes_and_promotes_a_successor():
    first = SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    services.archive_spot(first)
    second.refresh_from_db()
    assert SurfSpot.all_objects.get(pk=first.pk).is_deleted is True
    assert second.is_primary is True


def test_set_hazard_active_toggles_and_is_idempotent():
    hazard = SpotHazardFactory()
    services.set_hazard_active(hazard, is_active=False)
    hazard.refresh_from_db()
    assert hazard.is_active is False
    services.set_hazard_active(hazard, is_active=False)
    hazard.refresh_from_db()
    assert hazard.is_active is False


def test_spot_overview_stats():
    spot = SurfSpotFactory(name="Alpha", capacity=12, lifeguard_on_duty=True)
    SpotHazardFactory(spot=spot, severity=Severity.CRITICAL)
    SurfSpotFactory(name="Bravo", capacity=8, lifeguard_on_duty=False)

    stats = services.spot_overview_stats()
    assert stats["active"] == 2
    assert stats["lifeguarded"] == 1
    assert stats["at_risk"] == 1
    assert stats["total_capacity"] == 20
