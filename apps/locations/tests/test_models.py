from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.enums import Severity, SurfLevel, TideState, WindType
from apps.locations.models import SurfSpot, compass_label

from .factories import SpotHazardFactory, SurfSpotFactory

pytestmark = pytest.mark.django_db


def test_str_and_generated_identifiers():
    spot = SurfSpotFactory(name="Kabak Bay")
    assert str(spot) == "Kabak Bay"
    assert spot.slug == "kabak-bay"
    assert spot.code.startswith("SPOT")


def test_slug_is_uniquified():
    first = SurfSpotFactory(name="Twin Peaks")
    second = SurfSpotFactory(name="twin peaks!")
    assert first.slug == "twin-peaks"
    assert second.slug != first.slug


def test_first_spot_becomes_the_default():
    spot = SurfSpotFactory()
    assert spot.is_primary is True


def test_promoting_a_spot_demotes_the_previous_default():
    first = SurfSpotFactory()
    second = SurfSpotFactory(is_primary=True)
    first.refresh_from_db()
    assert second.is_primary is True
    assert first.is_primary is False
    assert SurfSpot.objects.filter(is_primary=True).count() == 1


def test_only_one_primary_can_be_written_directly():
    first = SurfSpotFactory()
    second = SurfSpotFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        SurfSpot.all_objects.filter(pk=second.pk).update(is_primary=True)
    assert SurfSpot.objects.filter(pk=first.pk, is_primary=True).exists()


def test_level_range_must_not_invert():
    spot = SurfSpotFactory.build(
        min_level=SurfLevel.ADVANCED, max_level=SurfLevel.BEGINNER
    )
    with pytest.raises(ValidationError) as error:
        spot.clean()
    assert "max_level" in error.value.message_dict


def test_inactive_spot_cannot_be_primary():
    spot = SurfSpotFactory.build(is_active=False, is_primary=True)
    with pytest.raises(ValidationError) as error:
        spot.clean()
    assert "is_primary" in error.value.message_dict


def test_bearing_must_be_a_compass_direction():
    spot = SurfSpotFactory.build(beach_facing_deg=421.0)
    with pytest.raises(ValidationError) as error:
        spot.clean()
    assert "beach_facing_deg" in error.value.message_dict


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (202.5, "SSW"), (359, "N")],
)
def test_compass_label(degrees, expected):
    assert compass_label(degrees) == expected


def test_wind_classification_uses_the_beach_bearing():
    # Beach faces south (180°), so offshore wind arrives from the north (0°).
    spot = SurfSpotFactory.build(beach_facing_deg=180.0)
    assert spot.classify_wind(0.0) == WindType.OFFSHORE
    assert spot.classify_wind(180.0) == WindType.ONSHORE
    assert spot.classify_wind(90.0) == WindType.CROSS_SHORE


def test_suits_level_respects_the_range():
    spot = SurfSpotFactory.build(
        min_level=SurfLevel.BEGINNER, max_level=SurfLevel.INTERMEDIATE
    )
    assert spot.suits_level(SurfLevel.BEGINNER)
    assert spot.suits_level(SurfLevel.INTERMEDIATE)
    assert not spot.suits_level(SurfLevel.FIRST_TIME)
    assert not spot.suits_level(SurfLevel.ADVANCED)


def test_archiving_the_default_hands_the_flag_on():
    first = SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    assert first.is_primary is True

    first.delete()

    second.refresh_from_db()
    assert second.is_primary is True
    assert not SurfSpot.objects.filter(pk=first.pk).exists()
    assert SurfSpot.all_objects.filter(pk=first.pk, is_deleted=True).exists()


def test_hazard_str_and_default_tide_window():
    hazard = SpotHazardFactory(name="Rip current", severity=Severity.HIGH)
    assert "Rip current" in str(hazard)
    assert hazard.has_tide_window is False
    assert hazard.applies_at_tide(TideState.HIGH) is True


def test_hazard_tide_window_wraps_around_the_cycle():
    hazard = SpotHazardFactory(
        applies_from_tide=TideState.MID_FALLING, applies_to_tide=TideState.LOW
    )
    assert hazard.applies_at_tide(TideState.MID_FALLING) is True
    assert hazard.applies_at_tide(TideState.LOW) is True
    assert hazard.applies_at_tide(TideState.HIGH) is False
    assert hazard.applies_at_tide(TideState.MID_RISING) is False


def test_hazard_with_unknown_tide_is_never_dismissed():
    hazard = SpotHazardFactory(
        applies_from_tide=TideState.LOW, applies_to_tide=TideState.LOW
    )
    assert hazard.applies_at_tide(TideState.UNKNOWN) is True
    assert hazard.applies_at_tide(None) is True


def test_hazard_rejects_unknown_as_a_window_bound():
    hazard = SpotHazardFactory.build(applies_from_tide=TideState.UNKNOWN)
    with pytest.raises(ValidationError) as error:
        hazard.clean()
    assert "applies_from_tide" in error.value.message_dict
