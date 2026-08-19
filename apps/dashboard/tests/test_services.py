"""Context building, capability gating, surf scoring and search scoping."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.core.enums import SurfLevel

from .. import selectors, services
from .conftest import (
    build,
    make_booking,
    make_customer,
    make_instructor,
    make_lesson,
    model_available,
)


def tile_keys(tiles) -> set[str]:
    return {tile["key"] for tile in tiles}


# ---------------------------------------------------------------------------
# An empty school must still render
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_empty_database_produces_a_context_without_errors(manager_user):
    context = services.build_dashboard_context(manager_user)
    assert context["dashboard_variant"] == "staff"
    assert context["today"] == timezone.localdate()
    assert isinstance(context["tiles"], list) and context["tiles"]


@pytest.mark.django_db
def test_zero_rows_report_zero_not_unknown(manager_user):
    """With the lessons module installed and no rows, the tile is a real 0."""
    if not model_available("lessons", "Lesson"):
        pytest.skip("lessons module not installed")
    tiles = {tile["key"]: tile for tile in services.build_tiles(manager_user, timezone.localdate())}
    assert tiles["todays_lessons"]["has_value"] is True
    assert tiles["todays_lessons"]["value"] == 0


@pytest.mark.django_db
def test_missing_payment_ledger_is_unknown_not_zero(manager_user, monkeypatch):
    """No ledger must never be rendered as "we took nothing today"."""
    monkeypatch.setattr(selectors, "_finance_selectors", lambda: None)
    monkeypatch.setattr(selectors, "_payment_source", lambda: None)
    tiles = {tile["key"]: tile for tile in services.build_tiles(manager_user, timezone.localdate())}
    revenue = tiles["todays_revenue"]
    assert revenue["value"] is None
    assert revenue["has_value"] is False
    assert revenue["note"]


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rental_clerk_sees_rentals_but_not_lessons(rental_clerk):
    keys = tile_keys(services.build_tiles(rental_clerk, timezone.localdate()))
    assert "active_rentals" in keys
    assert "todays_lessons" not in keys
    assert "todays_students" not in keys


@pytest.mark.django_db
def test_maintenance_staff_never_sees_money(maintenance_user):
    keys = tile_keys(services.build_tiles(maintenance_user, timezone.localdate()))
    assert "todays_revenue" not in keys
    assert "pending_payments" not in keys
    assert "equipment_warnings" in keys


@pytest.mark.django_db
def test_manager_sees_money(manager_user):
    keys = tile_keys(services.build_tiles(manager_user, timezone.localdate()))
    assert {"todays_revenue", "pending_payments"} <= keys


@pytest.mark.django_db
def test_panels_without_finance_do_not_query_balances(maintenance_user):
    context = services.build_dashboard_context(maintenance_user)
    assert "unpaid_bookings" not in context
    assert "outstanding_total" not in context
    assert context["revenue_chart"] is None


# ---------------------------------------------------------------------------
# Role dispatch
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_customer_gets_the_self_service_variant(customer_user):
    context = services.build_dashboard_context(customer_user)
    assert context["dashboard_variant"] == "customer"
    assert context["profile_missing"] is True
    assert "recent_activity" not in context


@pytest.mark.django_db
def test_instructor_with_a_profile_leads_with_their_own_lessons(instructor_user):
    mine = make_instructor(user=instructor_user)
    make_lesson(instructor=mine)
    make_lesson()  # somebody else's lesson, same day

    context = services.build_dashboard_context(instructor_user)
    assert context["dashboard_variant"] == "instructor"
    assert len(context["schedule"]) == 1
    assert context["schedule"][0]["lesson"].instructor_id == mine.pk
    assert context["school_lesson_count"] == 2


@pytest.mark.django_db
def test_instructor_without_a_profile_falls_back_to_the_staff_view(instructor_user):
    context = services.build_dashboard_context(instructor_user)
    assert context["dashboard_variant"] == "staff"


# ---------------------------------------------------------------------------
# Surf suitability scoring
# ---------------------------------------------------------------------------
def test_ideal_beginner_conditions_score_high():
    result = services.level_suitability_score(SurfLevel.BEGINNER, 0.6, 5.0)
    assert result["safe"] is True
    assert result["score"] >= 80


def test_waves_above_the_ceiling_are_a_hard_stop_for_beginners():
    result = services.level_suitability_score(SurfLevel.BEGINNER, 2.5, 5.0)
    assert result["safe"] is False
    assert result["score"] == 0


def test_the_same_swell_can_be_fine_for_advanced_surfers():
    result = services.level_suitability_score(SurfLevel.ADVANCED, 2.5, 5.0)
    assert result["safe"] is True
    assert result["score"] > 0


def test_wind_above_the_ceiling_is_a_hard_stop():
    result = services.level_suitability_score(SurfLevel.BEGINNER, 0.6, 45.0)
    assert result["safe"] is False
    assert result["score"] == 0


def test_unknown_wave_height_yields_no_score():
    assert services.level_suitability_score(SurfLevel.BEGINNER, None, 10.0) is None


@pytest.mark.django_db
def test_surf_panel_degrades_when_no_reading_exists():
    panel = services.surf_conditions_panel()
    assert panel["available"] is False
    assert panel["levels"] == []


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_search_needs_two_characters(manager_user):
    result = services.global_search(manager_user, "a")
    assert result["too_short"] is True
    assert result["groups"] == []


@pytest.mark.django_db
def test_empty_term_is_not_flagged_as_too_short(manager_user):
    result = services.global_search(manager_user, "")
    assert result["too_short"] is False
    assert result["total"] == 0


@pytest.mark.django_db
def test_search_finds_a_customer_by_name(manager_user):
    if not model_available("customers", "Customer"):
        pytest.skip("customers module not installed")
    customer = build("customers", "CustomerFactory", first_name="Deniz", last_name="Yilmaz")
    result = services.global_search(manager_user, "Deniz")
    groups = {group["key"]: group for group in result["groups"]}
    assert "customers" in groups
    assert any(customer.last_name in row["title"] for row in groups["customers"]["rows"])


@pytest.mark.django_db
def test_search_omits_groups_the_role_cannot_view(maintenance_user):
    if not model_available("customers", "Customer"):
        pytest.skip("customers module not installed")
    build("customers", "CustomerFactory", first_name="Deniz", last_name="Yilmaz")
    result = services.global_search(maintenance_user, "Deniz")
    assert all(group["key"] != "customers" for group in result["groups"])


@pytest.mark.django_db
def test_external_user_without_a_customer_record_searches_nothing(customer_user):
    if not model_available("customers", "Customer"):
        pytest.skip("customers module not installed")
    build("customers", "CustomerFactory", first_name="Deniz", last_name="Yilmaz")
    result = services.global_search(customer_user, "Deniz")
    assert result["groups"] == []
    assert result["total"] == 0


@pytest.mark.django_db
def test_external_user_cannot_search_the_customer_directory(customer_user):
    """A customer holds no ``customers.view``, so that group is never queried."""
    if not model_available("customers", "Customer"):
        pytest.skip("customers module not installed")
    mine = build("customers", "CustomerFactory", first_name="Deniz", last_name="Yilmaz")
    mine.user = customer_user
    mine.save(update_fields=["user"])
    build("customers", "CustomerFactory", first_name="Deniz", last_name="Kaya")

    result = services.global_search(customer_user, "Deniz")
    assert all(group["key"] != "customers" for group in result["groups"])


@pytest.mark.django_db
def test_external_user_only_finds_their_own_bookings(customer_user):
    """Scoping happens before the query: another customer's booking is invisible."""
    mine = make_booking(customer=make_customer(user=customer_user))
    theirs = make_booking()

    result = services.global_search(customer_user, "BK")
    groups = {group["key"]: group for group in result["groups"]}
    titles = [row["title"] for row in groups.get("bookings", {"rows": []})["rows"]]
    assert mine.booking_code in titles
    assert theirs.booking_code not in titles


@pytest.mark.django_db
def test_direct_hit_is_disabled_for_external_users(customer_user):
    assert selectors.direct_hit_url(customer_user, "EQP00001") is None


# ---------------------------------------------------------------------------
# With real rows in the database
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_schedule_counts_seats_and_pending_check_ins(manager_user):
    Attendance = pytest.importorskip("apps.lessons.models").LessonAttendance
    lesson = make_lesson()
    customer = make_customer()
    Student = pytest.importorskip("apps.students.models").Student
    student = Student.objects.create(customer=customer)
    Attendance.objects.create(
        lesson=lesson, student=student, status=Attendance.Status.REGISTERED
    )

    rows = services.todays_schedule(timezone.localdate())
    assert len(rows) == 1
    assert rows[0]["booked"] == 1
    assert rows[0]["awaiting_check_in"] == 1
    assert rows[0]["capacity"] == lesson.capacity
    assert rows[0]["fill_percent"] == round(100 / lesson.capacity)


@pytest.mark.integration
@pytest.mark.django_db
def test_todays_lesson_appears_in_the_tile(manager_user):
    make_lesson()
    tiles = {tile["key"]: tile for tile in services.build_tiles(manager_user, timezone.localdate())}
    assert tiles["todays_lessons"]["value"] == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_yesterdays_lesson_is_not_counted_as_today(manager_user):
    from datetime import timedelta

    make_lesson(day=timezone.localdate() - timedelta(days=1))
    tiles = {tile["key"]: tile for tile in services.build_tiles(manager_user, timezone.localdate())}
    assert tiles["todays_lessons"]["value"] == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_unpaid_booking_raises_the_pending_payments_total(manager_user):
    from decimal import Decimal

    from apps.core.enums import BookingStatus, PaymentStatus

    make_booking(
        status=BookingStatus.CONFIRMED,
        payment_status=PaymentStatus.PARTIAL,
        unit_price=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        paid_amount=Decimal("200.00"),
    )
    assert selectors.outstanding_booking_balance() == Decimal("300.00")

    tiles = {tile["key"]: tile for tile in services.build_tiles(manager_user, timezone.localdate())}
    assert tiles["pending_payments"]["value"] >= Decimal("300.00")


@pytest.mark.integration
@pytest.mark.django_db
def test_revenue_sparkline_covers_the_whole_window(manager_user):
    """Even days with no payments appear, so the line has no gaps."""
    if not model_available("finance", "Payment"):
        pytest.skip("finance module not installed")
    chart = services.revenue_sparkline(timezone.localdate())
    assert chart is not None
    assert len(chart["labels"]) == services.REVENUE_WINDOW_DAYS
    assert len(chart["values"]) == services.REVENUE_WINDOW_DAYS
