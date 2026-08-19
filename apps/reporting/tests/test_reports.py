"""The report catalogue: every builder must produce a usable document."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import EquipmentStatus, SurfLevel
from apps.reporting.cron import CronError, cron_is_due, parse_cron
from apps.reporting.exporters import get_exporter
from apps.reporting.exporters.base import ReportData
from apps.reporting.reports import (
    REGISTRY,
    all_reports,
    field_path_exists,
    first_path,
    get_model,
    grouped_reports,
    reports_for_user,
    resolve_period,
    sum_field,
)

pytestmark = pytest.mark.django_db
User = get_user_model()

#: Every report named in the module brief must exist in the catalogue.
REQUIRED_KEYS = {
    "daily_operations",
    "revenue_report",
    "payments_report",
    "expenses_report",
    "profit_loss",
    "bookings_report",
    "cancellations_report",
    "student_list",
    "student_progress",
    "instructor_performance",
    "instructor_commission",
    "equipment_inventory",
    "equipment_utilisation",
    "maintenance_report",
    "rental_report",
    "overdue_rentals",
    "surf_camp_roster",
    "camp_financials",
    "safety_incidents",
    "customer_list",
}


@pytest.fixture
def super_admin(db):
    return User.objects.create_user(
        username="boss",
        email="boss@example.com",
        password="pw-test-12345",
        role=Role.SUPER_ADMIN,
    )


@pytest.fixture
def rental_staff(db):
    return User.objects.create_user(
        username="hire",
        email="hire@example.com",
        password="pw-test-12345",
        role=Role.RENTAL_STAFF,
    )


# ---------------------------------------------------------------------------
# Catalogue integrity
# ---------------------------------------------------------------------------
def test_every_required_report_is_registered():
    assert set(REGISTRY) >= REQUIRED_KEYS


def test_every_report_declares_a_real_capability():
    from apps.accounts.constants import all_capabilities

    known = all_capabilities()
    for spec in all_reports():
        assert spec.capability in known, spec.key


def test_catalogue_is_filtered_by_capability(super_admin, rental_staff):
    admin_keys = {spec.key for spec in reports_for_user(super_admin)}
    staff_keys = {spec.key for spec in reports_for_user(rental_staff)}

    assert admin_keys >= REQUIRED_KEYS
    # Rental staff see equipment and hire reports. The financial statements need
    # finance.export and the customer list needs customers.export; they hold
    # neither, so those must not appear.
    assert "rental_report" in staff_keys
    assert "equipment_inventory" in staff_keys
    assert "profit_loss" not in staff_keys
    assert "revenue_report" not in staff_keys
    assert "customer_list" not in staff_keys


def test_anonymous_users_see_nothing():
    from django.contrib.auth.models import AnonymousUser

    assert reports_for_user(AnonymousUser()) == []
    assert grouped_reports(AnonymousUser()) == []


def test_groups_are_ordered_and_non_empty(super_admin):
    groups = grouped_reports(super_admin)
    assert groups
    for _key, _label, _icon, specs in groups:
        assert specs


# ---------------------------------------------------------------------------
# Builders on an empty database
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
def test_builder_returns_report_data_on_an_empty_database(key, super_admin):
    """No module has data yet; a report must explain that, never crash."""
    data = REGISTRY[key].build(super_admin, {})
    assert isinstance(data, ReportData)
    assert data.title
    # An empty report always says why it is empty.
    assert data.is_empty is False or data.message


@pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
@pytest.mark.parametrize("fmt", ["pdf", "excel", "csv"])
def test_every_report_renders_in_every_format(key, fmt, super_admin):
    data = REGISTRY[key].build(super_admin, {})
    payload = get_exporter(fmt).render(data)
    assert isinstance(payload, bytes)
    assert payload


def test_a_report_for_an_uninstalled_module_says_so(super_admin):
    """finance/safety may not be deployed; those reports degrade politely."""
    if get_model("finance.Invoice") is not None:
        pytest.skip("The finance module is installed in this build.")
    data = REGISTRY["revenue_report"].build(super_admin, {})
    assert data.is_empty
    assert "not installed" in data.message


# ---------------------------------------------------------------------------
# Builders with data
# ---------------------------------------------------------------------------
def test_equipment_inventory_lists_and_totals_the_fleet(super_admin):
    equipment_model = get_model("equipment.Equipment")
    category_model = get_model("equipment.EquipmentCategory")
    if equipment_model is None:
        pytest.skip("The equipment module is not installed.")

    category = category_model.objects.create(code="boards", name="Boards")
    for index in range(3):
        equipment_model.objects.create(
            asset_code=f"BRD-{index:03d}",
            category=category,
            name=f"Soft top {index}",
            status=EquipmentStatus.AVAILABLE,
            purchase_price=Decimal("400.00"),
            current_value=Decimal("250.00"),
        )

    data = REGISTRY["equipment_inventory"].build(super_admin, {})
    assert data.row_count == 3
    assert "Asset code" in data.columns or data.columns
    assert data.summary


def test_customer_list_always_carries_the_consent_column(super_admin):
    customer_model = get_model("customers.Customer")
    if customer_model is None:
        pytest.skip("The customers module is not installed.")

    customer_model.objects.create(
        customer_code="CUS00001",
        first_name="Ayşe",
        last_name="Yılmaz",
        email="ayse@example.com",
        marketing_consent=False,
    )
    customer_model.objects.create(
        customer_code="CUS00002",
        first_name="Mehmet",
        last_name="Demir",
        email="mehmet@example.com",
        marketing_consent=True,
    )

    data = REGISTRY["customer_list"].build(super_admin, {"range": "all"})
    assert data.row_count == 2
    assert any("consent" in str(column).lower() for column in data.columns)
    # The export warns about the rows that must not be marketed to.
    assert "NOT consented" in data.message


def test_customer_list_can_be_narrowed_to_consenting_customers(super_admin):
    customer_model = get_model("customers.Customer")
    if customer_model is None:
        pytest.skip("The customers module is not installed.")

    customer_model.objects.create(
        customer_code="CUS00003", first_name="A", last_name="B", marketing_consent=False
    )
    customer_model.objects.create(
        customer_code="CUS00004", first_name="C", last_name="D", marketing_consent=True
    )

    data = REGISTRY["customer_list"].build(
        super_admin, {"range": "all", "marketing_only": True}
    )
    assert data.row_count == 1


def test_student_list_honours_the_level_filter(super_admin):
    student_model = get_model("students.Student")
    customer_model = get_model("customers.Customer")
    if student_model is None or customer_model is None:
        pytest.skip("The students module is not installed.")

    for index, level in enumerate((SurfLevel.BEGINNER, SurfLevel.ADVANCED)):
        customer = customer_model.objects.create(
            customer_code=f"CUS1000{index}", first_name=f"S{index}", last_name="Test"
        )
        student_model.objects.create(
            customer=customer, student_code=f"STU0000{index}", surf_level=level
        )

    everyone = REGISTRY["student_list"].build(super_admin, {"range": "all"})
    beginners = REGISTRY["student_list"].build(
        super_admin, {"range": "all", "level": SurfLevel.BEGINNER}
    )
    assert everyone.row_count == 2
    assert beginners.row_count == 1


# ---------------------------------------------------------------------------
# Field probing & periods
# ---------------------------------------------------------------------------
def test_field_path_exists_walks_relations():
    booking = get_model("bookings.Booking")
    if booking is None:
        pytest.skip("The bookings module is not installed.")
    assert field_path_exists(booking, "customer__first_name")
    assert not field_path_exists(booking, "customer__no_such_field")
    assert not field_path_exists(booking, "booking_code__nested")


def test_first_path_picks_the_field_that_exists():
    booking = get_model("bookings.Booking")
    if booking is None:
        pytest.skip("The bookings module is not installed.")
    assert first_path(booking, "invented", "booked_at") == "booked_at"
    assert first_path(booking, "invented", "also_invented") is None


def test_sum_field_returns_zero_for_a_missing_field():
    booking = get_model("bookings.Booking")
    if booking is None:
        pytest.skip("The bookings module is not installed.")
    assert sum_field(booking.objects.all(), "no_such_amount") == Decimal("0.00")
    assert sum_field(booking.objects.all(), "total_amount") == Decimal("0.00")


def test_resolve_period_understands_the_shared_vocabulary():
    period = resolve_period({"range": "7"})
    assert period.days == 7
    assert period.start is not None

    all_time = resolve_period({"range": "all"})
    assert all_time.start is None and all_time.end is None


def test_custom_period_swaps_reversed_dates():
    today = timezone.localdate()
    period = resolve_period(
        {
            "range": "custom",
            "start": today.isoformat(),
            "end": (today - timedelta(days=5)).isoformat(),
        }
    )
    assert period.start < period.end


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------
def test_cron_parses_the_common_shapes():
    assert 0 in parse_cron("0 7 * * 1").minutes
    assert parse_cron("*/15 * * * *").minutes == frozenset({0, 15, 30, 45})
    assert parse_cron("0 9-17 * * 1-5").hours == frozenset(range(9, 18))
    assert parse_cron("0 6,18 * * *").hours == frozenset({6, 18})


@pytest.mark.parametrize(
    "expression",
    ["", "0 7 * *", "60 7 * * *", "0 25 * * *", "a b c d e", "0 7 * * 1/0"],
)
def test_cron_rejects_nonsense(expression):
    with pytest.raises(CronError):
        parse_cron(expression)


def test_cron_is_due_matches_the_minute():
    from datetime import datetime

    monday_seven = datetime(2026, 8, 17, 7, 0)  # a Monday
    assert cron_is_due("0 7 * * 1", monday_seven)
    assert not cron_is_due("0 7 * * 2", monday_seven)


def test_cron_window_catches_a_scheduler_that_ticks_late():
    from datetime import datetime

    monday_seven_oh_four = datetime(2026, 8, 17, 7, 4)
    assert not cron_is_due("0 7 * * 1", monday_seven_oh_four, window_minutes=1)
    assert cron_is_due("0 7 * * 1", monday_seven_oh_four, window_minutes=5)


def test_an_unparseable_schedule_is_never_due():
    assert cron_is_due("nonsense", timezone.localtime()) is False
