"""Model-level tests: identifiers, validation and the derived properties."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import EquipmentCondition, EquipmentStatus, SurfLevel

from ..models import QR_PREFIX, Equipment
from .factories import (
    EquipmentCategoryFactory,
    EquipmentFactory,
    RentableEquipmentFactory,
)

pytestmark = pytest.mark.django_db


def test_asset_code_is_generated_and_sequential():
    first = EquipmentFactory()
    second = EquipmentFactory()
    assert first.asset_code == "EQ00001"
    assert second.asset_code == "EQ00002"


def test_str_includes_code_and_size():
    item = EquipmentFactory(name="Soft-top", size_label="8'0\"")
    assert item.asset_code in str(item)
    assert "Soft-top" in str(item)


def test_category_full_path_walks_the_tree():
    parent = EquipmentCategoryFactory(code="surfboard", name="Surfboard")
    child = EquipmentCategoryFactory(code="softboard", name="Softboard", parent=parent)
    assert child.full_path == "Surfboard › Softboard"
    assert parent.pk in child.descendant_ids or child.pk in parent.descendant_ids


def test_category_rejects_a_parent_loop():
    parent = EquipmentCategoryFactory(code="a", name="A")
    child = EquipmentCategoryFactory(code="b", name="B", parent=parent)
    parent.parent = child
    with pytest.raises(ValidationError):
        parent.full_clean()


def test_qr_payload_uses_the_public_id_not_the_pk():
    item = EquipmentFactory()
    assert item.qr_payload == f"{QR_PREFIX}{item.public_id}"
    # The payload must be the UUID, so a scanned label cannot be enumerated.
    assert item.qr_payload.removeprefix(QR_PREFIX) == str(item.public_id)


def test_qr_svg_renders_an_svg_document():
    svg = EquipmentFactory().qr_svg()
    assert "<svg" in svg


def test_rentable_item_needs_a_price():
    item = EquipmentFactory.build(is_rentable=True, category=EquipmentCategoryFactory())
    with pytest.raises(ValidationError) as error:
        item.full_clean()
    assert "is_rentable" in error.value.message_dict


def test_rentable_item_with_a_price_validates():
    item = RentableEquipmentFactory.build(category=EquipmentCategoryFactory())
    item.full_clean()  # must not raise


def test_level_range_must_not_be_inverted():
    item = EquipmentFactory.build(
        category=EquipmentCategoryFactory(),
        suitable_min_level=SurfLevel.ADVANCED,
        suitable_max_level=SurfLevel.BEGINNER,
    )
    with pytest.raises(ValidationError) as error:
        item.full_clean()
    assert "suitable_max_level" in error.value.message_dict


def test_unusable_item_cannot_be_in_circulation():
    item = EquipmentFactory.build(
        category=EquipmentCategoryFactory(),
        condition=EquipmentCondition.UNUSABLE,
        status=EquipmentStatus.AVAILABLE,
    )
    with pytest.raises(ValidationError) as error:
        item.full_clean()
    assert "status" in error.value.message_dict


def test_retired_item_requires_a_reason():
    item = EquipmentFactory.build(
        category=EquipmentCategoryFactory(), status=EquipmentStatus.RETIRED
    )
    with pytest.raises(ValidationError) as error:
        item.full_clean()
    assert "retired_reason" in error.value.message_dict


def test_is_available_reflects_status_and_condition():
    item = EquipmentFactory()
    assert item.is_available is True
    item.status = EquipmentStatus.RENTED
    assert item.is_available is False


def test_needs_maintenance_when_service_date_has_passed():
    item = EquipmentFactory(
        next_maintenance_date=timezone.localdate() - timedelta(days=1)
    )
    assert item.needs_maintenance is True


def test_depreciation_and_age():
    item = EquipmentFactory(
        purchase_date=timezone.localdate() - timedelta(days=100),
        purchase_price=Decimal("1000.00"),
        current_value=Decimal("750.00"),
    )
    assert item.age_days == 100
    assert item.depreciation_percent == Decimal("25.00")


def test_utilisation_rate_is_capped_at_one_hundred():
    item = EquipmentFactory(
        purchase_date=timezone.localdate() - timedelta(days=10),
        total_rental_hours=Decimal("500.00"),
    )
    assert item.utilisation_rate == Decimal("100.00")


def test_soft_delete_hides_the_row_but_keeps_it():
    item = EquipmentFactory()
    item.delete()
    assert not Equipment.objects.filter(pk=item.pk).exists()
    assert Equipment.all_objects.filter(pk=item.pk).exists()
