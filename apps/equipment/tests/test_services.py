"""Service tests: availability, recommendations, the state machine and imports."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.core.enums import EquipmentCondition, EquipmentStatus, SurfLevel

from ..models import Equipment
from ..services import (
    DEFAULT_CATEGORIES,
    archive_equipment,
    available_equipment,
    bulk_import_from_rows,
    change_status,
    ensure_default_categories,
    fleet_summary,
    generate_asset_code,
    recommend_board,
    recommend_wetsuit,
    register_rental_usage,
    utilisation_report,
)
from .factories import (
    EquipmentCategoryFactory,
    EquipmentFactory,
    SoftboardCategoryFactory,
    UserFactory,
    WetsuitFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------- categories
def test_seeding_categories_is_idempotent():
    created, untouched = ensure_default_categories()
    assert created == len(DEFAULT_CATEGORIES)
    assert untouched == 0

    created_again, untouched_again = ensure_default_categories()
    assert created_again == 0
    assert untouched_again == len(DEFAULT_CATEGORIES)


def test_seeded_softboard_hangs_under_surfboard():
    ensure_default_categories()
    from ..models import EquipmentCategory

    softboard = EquipmentCategory.objects.get(code="softboard")
    assert softboard.full_path == "Surfboard › Softboard"


def test_generate_asset_code_continues_the_series():
    EquipmentFactory()
    assert generate_asset_code(None) == "EQ00002"


# -------------------------------------------------------------- availability
def test_available_equipment_excludes_unavailable_statuses():
    ready = EquipmentFactory()
    EquipmentFactory(status=EquipmentStatus.MAINTENANCE)
    EquipmentFactory(status=EquipmentStatus.RETIRED, retired_reason="snapped")
    EquipmentFactory(status=EquipmentStatus.DAMAGED)

    codes = set(available_equipment().values_list("asset_code", flat=True))
    assert codes == {ready.asset_code}


def test_available_equipment_excludes_items_currently_out():
    EquipmentFactory(status=EquipmentStatus.RENTED)
    assert available_equipment().count() == 0


def test_available_equipment_filters_by_level_and_weight():
    beginner_board = EquipmentFactory(
        suitable_min_level=SurfLevel.FIRST_TIME,
        suitable_max_level=SurfLevel.BEGINNER,
        min_rider_weight_kg=Decimal("50.0"),
        max_rider_weight_kg=Decimal("90.0"),
    )
    EquipmentFactory(
        suitable_min_level=SurfLevel.ADVANCED,
        suitable_max_level=SurfLevel.COMPETITION,
    )

    result = available_equipment(level=SurfLevel.BEGINNER, rider_weight_kg=75)
    assert list(result.values_list("pk", flat=True)) == [beginner_board.pk]

    # Too heavy for the only suitable board.
    assert available_equipment(level=SurfLevel.BEGINNER, rider_weight_kg=120).count() == 0


def test_available_equipment_with_no_rentals_app_returns_everything_free():
    EquipmentFactory()
    # rentals is not installed in this project stage: zero rows means available.
    assert available_equipment().count() == 1


# ----------------------------------------------------------- recommendations
def test_recommend_board_prefers_the_closest_volume():
    category = SoftboardCategoryFactory()
    close = EquipmentFactory(category=category, volume_litres=Decimal("75.00"))
    EquipmentFactory(category=category, volume_litres=Decimal("40.00"))

    result = recommend_board(75, SurfLevel.FIRST_TIME)
    assert result.equipment == close
    assert result.target_volume_litres == 75.0
    assert close.asset_code in result.reasoning


def test_recommend_board_enforces_soft_tops_for_beginners():
    hard_board = EquipmentCategoryFactory(code="shortboard", name="Shortboard")
    EquipmentFactory(category=hard_board, volume_litres=Decimal("75.00"))
    SoftboardCategoryFactory()  # exists but empty

    result = recommend_board(75, SurfLevel.FIRST_TIME)
    assert result.equipment is None
    assert result.soft_top_required is True
    assert "soft-top" in result.reasoning.lower()


def test_recommend_board_allows_hard_boards_for_advanced_riders():
    shortboard = EquipmentCategoryFactory(code="shortboard", name="Shortboard")
    board = EquipmentFactory(
        category=shortboard,
        volume_litres=Decimal("31.00"),
        suitable_min_level=SurfLevel.INTERMEDIATE,
        suitable_max_level=SurfLevel.COMPETITION,
    )
    result = recommend_board(75, SurfLevel.ADVANCED)
    assert result.equipment == board
    assert result.soft_top_required is False


def test_recommend_wetsuit_matches_size_and_lists_accessories():
    WetsuitFactory(size_label="M", wetsuit_thickness="4/3")
    result = recommend_wetsuit(9.0, "M")
    assert result.equipment is not None
    assert "Hood" in result.required_accessories
    assert "Gloves" in result.required_accessories


def test_recommend_wetsuit_reports_when_nothing_fits():
    WetsuitFactory(size_label="L")
    result = recommend_wetsuit(16.0, "XS")
    assert result.equipment is None
    assert "XS" in result.reasoning


# ------------------------------------------------------------ state machine
def test_change_status_records_an_audit_entry():
    user = UserFactory()
    item = EquipmentFactory()
    change_status(item, EquipmentStatus.MAINTENANCE, user=user, reason="Deck ding")
    item.refresh_from_db()

    assert item.status == EquipmentStatus.MAINTENANCE
    assert AuditLog.objects.filter(object_id=str(item.pk)).exists()


def test_change_status_rejects_an_illegal_move():
    item = EquipmentFactory(status=EquipmentStatus.RENTED)
    with pytest.raises(ValidationError):
        change_status(item, EquipmentStatus.RETIRED, user=None, reason="worn out")


def test_change_status_requires_a_reason_for_damage():
    item = EquipmentFactory()
    with pytest.raises(ValidationError) as error:
        change_status(item, EquipmentStatus.DAMAGED, user=None, reason="")
    assert "reason" in error.value.message_dict


def test_change_status_refuses_to_release_an_unusable_item():
    item = EquipmentFactory(
        status=EquipmentStatus.MAINTENANCE, condition=EquipmentCondition.UNUSABLE
    )
    with pytest.raises(ValidationError):
        change_status(item, EquipmentStatus.AVAILABLE, user=None, reason="repaired")


def test_retiring_stamps_the_reason_and_date():
    item = EquipmentFactory()
    change_status(item, EquipmentStatus.RETIRED, user=None, reason="Snapped in half")
    item.refresh_from_db()
    assert item.retired_at is not None
    assert item.retired_reason == "Snapped in half"


def test_returning_to_service_clears_the_retirement():
    item = EquipmentFactory()
    change_status(item, EquipmentStatus.RETIRED, user=None, reason="Snapped")
    change_status(item, EquipmentStatus.AVAILABLE, user=None, reason="Rebuilt")
    item.refresh_from_db()
    assert item.retired_at is None
    assert item.retired_reason == ""


def test_same_status_is_a_no_op():
    item = EquipmentFactory()
    change_status(item, EquipmentStatus.AVAILABLE, user=None, reason="")
    assert item.status == EquipmentStatus.AVAILABLE


def test_archive_refuses_while_the_item_is_out():
    item = EquipmentFactory(status=EquipmentStatus.RENTED)
    with pytest.raises(ValidationError):
        archive_equipment(item)


def test_archive_soft_deletes():
    item = EquipmentFactory()
    archive_equipment(item)
    assert not Equipment.objects.filter(pk=item.pk).exists()


# ------------------------------------------------------------------ counters
def test_register_rental_usage_bumps_the_counters():
    item = EquipmentFactory()
    register_rental_usage(item, Decimal("3.50"))
    item.refresh_from_db()
    assert item.total_rentals == 1
    assert item.total_rental_hours == Decimal("3.50")


# ------------------------------------------------------------- utilisation
def test_utilisation_report_falls_back_to_lifetime_counters():
    EquipmentFactory(total_rentals=4, total_rental_hours=Decimal("20.00"))
    rows = utilisation_report()
    assert len(rows) == 1
    assert rows[0]["rentals"] == 4
    assert rows[0]["is_lifetime"] is True


def test_fleet_summary_counts_each_bucket():
    EquipmentFactory()
    EquipmentFactory(status=EquipmentStatus.RENTED)
    EquipmentFactory(status=EquipmentStatus.MAINTENANCE)
    summary = fleet_summary()
    assert summary["total"] == 3
    assert summary["available"] == 1
    assert summary["out"] == 1
    assert summary["maintenance"] == 1


# ---------------------------------------------------------------- CSV import
def _row(**overrides):
    row = {
        "asset_code": "",
        "category_code": "softboard",
        "name": "Soft-top 8'0",
        "brand": "Demo Boards",
        "model": "Mod Fun",
        "serial_number": "SN-1",
        "size_label": "8'0",
        "length_cm": "244",
        "width_cm": "56",
        "thickness_cm": "8",
        "volume_litres": "68",
        "wetsuit_thickness": "",
        "suitable_min_level": "first_time",
        "suitable_max_level": "beginner",
        "min_rider_weight_kg": "40",
        "max_rider_weight_kg": "95",
        "purchase_date": "2025-04-01",
        "purchase_price": "9500",
        "current_value": "7200",
        "supplier": "Demo Boards",
        "status": "available",
        "condition": "good",
        "storage_location": "Container A",
        "is_rentable": "no",
        "is_lesson_stock": "yes",
        "rental_price_hourly": "0",
        "rental_price_daily": "0",
        "rental_price_weekly": "0",
        "deposit_amount": "0",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_import_dry_run_writes_nothing():
    SoftboardCategoryFactory()
    result = bulk_import_from_rows([_row()], dry_run=True)
    assert result.created == 1
    assert Equipment.objects.count() == 0


def test_import_creates_rows_after_confirmation():
    SoftboardCategoryFactory()
    bulk_import_from_rows([_row(), _row(name="Soft-top 7'6")], dry_run=False)
    assert Equipment.objects.count() == 2


def test_import_reports_an_unknown_category():
    result = bulk_import_from_rows([_row(category_code="nope")], dry_run=True)
    assert result.errors == 1
    assert "nope" in result.rows[0].message


def test_import_reports_a_bad_number():
    SoftboardCategoryFactory()
    result = bulk_import_from_rows([_row(volume_litres="huge")], dry_run=True)
    assert result.errors == 1


def test_import_updates_an_existing_asset_code():
    SoftboardCategoryFactory()
    existing = EquipmentFactory(category=SoftboardCategoryFactory(), name="Old name")
    result = bulk_import_from_rows(
        [_row(asset_code=existing.asset_code, name="New name")], dry_run=False
    )
    existing.refresh_from_db()
    assert result.updated == 1
    assert existing.name == "New name"


def test_import_keeps_good_rows_when_one_row_fails():
    SoftboardCategoryFactory()
    result = bulk_import_from_rows(
        [_row(), _row(category_code="missing"), _row(name="Third")], dry_run=False
    )
    assert result.created == 2
    assert result.errors == 1
    assert Equipment.objects.count() == 2
