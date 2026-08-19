"""Metric service tests.

These build real rows in the sibling apps and assert on the numbers analytics
derives from them. Where a figure depends on which modules are installed
(revenue can come from the finance ledger or from the operational modules), the
test pins the source explicitly rather than assuming one, so it stays honest as
the rest of the project lands.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.analytics import services
from apps.core.enums import BookingSource, BookingStatus, LessonStatus, SurfLevel

from .factories import (
    build_booking,
    build_customer,
    build_equipment,
    build_instructor,
    build_lesson,
    build_lesson_type,
    build_payment,
    build_rental,
    build_rental_item,
    build_student,
)

pytestmark = pytest.mark.django_db


def day_window(days_back: int = 6):
    """``[start, end]`` covering the last *days_back + 1* whole local days."""
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(today - timedelta(days=days_back), time.min), tz
    )
    end = timezone.make_aware(datetime.combine(today, time.max), tz)
    return start, end


# ---------------------------------------------------------------------------
# Range and bucket plumbing
# ---------------------------------------------------------------------------
def test_normalise_range_fills_in_a_missing_start():
    end = timezone.now()
    start, resolved_end = services.normalise_range(None, end)
    assert resolved_end == end
    assert (end - start).days == 364


def test_normalise_range_orders_a_reversed_pair():
    end = timezone.now()
    start = end - timedelta(days=3)
    assert services.normalise_range(end, start) == (start, end)


def test_bucket_choice_follows_the_span():
    start, end = day_window(6)
    assert services.choose_bucket(start, end) == "day"
    assert services.choose_bucket(end - timedelta(days=200), end) == "week"
    assert services.choose_bucket(end - timedelta(days=800), end) == "month"


def test_daily_buckets_have_no_holes():
    start, end = day_window(6)
    assert len(services.bucket_starts(start, end, "day")) == 7


def test_weekly_buckets_start_on_monday():
    start, end = day_window(20)
    for moment in services.bucket_starts(start, end, "week"):
        assert moment.weekday() == 0


# ---------------------------------------------------------------------------
# Empty database — nothing may crash, everything reports no_data
# ---------------------------------------------------------------------------
EMPTY_SAFE_METRICS = (
    "revenue_metrics",
    "booking_metrics",
    "lesson_occupancy",
    "customer_metrics",
    "instructor_metrics",
    "equipment_utilisation",
    "rental_metrics",
    "student_level_distribution",
    "channel_mix",
    "busiest_hours",
    "busiest_weekdays",
)


@pytest.mark.parametrize("name", EMPTY_SAFE_METRICS)
def test_every_metric_survives_an_empty_school(name):
    start, end = day_window()
    metric = getattr(services, name)(start, end)
    assert metric["no_data"] is True
    assert float(metric["current"] or 0) == 0.0
    assert isinstance(metric["series"], list)


def test_dashboard_metrics_returns_every_key():
    start, end = day_window()
    metrics = services.dashboard_metrics(start, end)
    assert set(metrics) == {
        "revenue",
        "bookings",
        "occupancy",
        "customers",
        "instructors",
        "equipment",
        "rentals",
        "levels",
        "channels",
        "hours",
        "weekdays",
    }


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------
def test_the_finance_ledger_is_used_alone_so_money_is_never_counted_twice():
    sources = services._revenue_sources()
    labels = {source["source"] for source in sources}
    if "finance" in labels:
        assert len(sources) == 1, "the ledger must not be combined with operational amounts"
    else:
        assert "bookings" in labels or not sources


def test_revenue_sums_and_buckets_the_configured_source(monkeypatch):
    """Pin the source to bookings so the arithmetic is deterministic."""
    booking_model = services._model("bookings.Booking")
    monkeypatch.setattr(
        services,
        "_revenue_sources",
        lambda: [
            {
                "model": booking_model,
                "amount": "paid_amount",
                "date": "booked_at",
                "label": "Booking payments",
                "source": "bookings",
                "exclude": {"status": BookingStatus.CANCELLED},
            }
        ],
    )

    start, end = day_window(6)
    build_booking(booked_at=end - timedelta(hours=2), paid=Decimal("100.00"))
    build_booking(booked_at=end - timedelta(hours=1), paid=Decimal("50.00"))
    # Excluded: cancelled money was never taken.
    build_booking(
        booked_at=end - timedelta(hours=1),
        paid=Decimal("999.00"),
        status=BookingStatus.CANCELLED,
    )
    # Outside the window.
    build_booking(booked_at=start - timedelta(days=3), paid=Decimal("777.00"))

    metric = services.revenue_metrics(start, end)
    assert metric["current"] == Decimal("150.00")
    assert metric["no_data"] is False
    assert len(metric["series"]) == 7
    assert sum(point["value"] for point in metric["series"]) == pytest.approx(150.0)


@pytest.mark.skipif(
    services._model("finance.Payment") is None,
    reason="the finance module is not installed in this build",
)
def test_revenue_reads_the_ledger_net_of_refunds():
    """A refund is a negative row, so the sum is already the net figure."""
    start, end = day_window(6)
    build_payment(amount=Decimal("200.00"), paid_at=end - timedelta(hours=2))
    build_payment(amount=Decimal("-50.00"), paid_at=end - timedelta(hours=1))
    build_payment(amount=Decimal("999.00"), paid_at=start - timedelta(days=2))

    metric = services.revenue_metrics(start, end)
    assert metric["source"] == "finance"
    assert metric["current"] == Decimal("150.00")
    assert metric["no_data"] is False


def test_revenue_series_can_be_forced_to_daily_buckets(monkeypatch):
    booking_model = services._model("bookings.Booking")
    monkeypatch.setattr(
        services,
        "_revenue_sources",
        lambda: [
            {
                "model": booking_model,
                "amount": "paid_amount",
                "date": "booked_at",
                "label": "Booking payments",
                "source": "bookings",
                "exclude": {},
            }
        ],
    )
    start, end = day_window(120)
    assert services.revenue_metrics(start, end)["bucket"] == "week"
    assert services.revenue_metrics(start, end, bucket="day")["bucket"] == "day"


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------
def test_booking_rates_and_lead_time():
    start, end = day_window(6)
    today = timezone.localdate()
    lesson = build_lesson(on_date=today + timedelta(days=10))

    for _index in range(6):
        build_booking(booked_at=end - timedelta(hours=3), lesson=lesson)
    build_booking(
        booked_at=end - timedelta(hours=3), lesson=lesson, status=BookingStatus.CANCELLED
    )
    build_booking(
        booked_at=end - timedelta(hours=3), lesson=lesson, status=BookingStatus.NO_SHOW
    )
    build_booking(booked_at=end - timedelta(hours=3), lesson=lesson)
    build_booking(booked_at=end - timedelta(hours=3), lesson=lesson)

    metric = services.booking_metrics(start, end)
    assert metric["current"] == 10
    assert metric["cancelled"] == 1
    assert metric["no_show"] == 1
    assert metric["cancellation_rate"] == 10.0
    assert metric["no_show_rate"] == 10.0
    # Every booking was taken today for a lesson ten days out.
    assert metric["average_lead_days"] == pytest.approx(10.0)
    assert metric["lead_time_sample"] == 10


def test_booking_status_breakdown_lists_every_status():
    start, end = day_window()
    metric = services.booking_metrics(start, end)
    assert len(metric["status_breakdown"]) == len(BookingStatus.choices)


def test_bookings_compare_against_the_previous_period():
    start, end = day_window(6)
    previous_start, _previous_end = services.previous_period(start, end)
    build_booking(booked_at=end - timedelta(hours=1))
    build_booking(booked_at=previous_start + timedelta(hours=1))
    build_booking(booked_at=previous_start + timedelta(hours=2))

    metric = services.booking_metrics(start, end)
    assert metric["current"] == 1
    assert metric["previous"] == 2
    assert metric["change_pct"] == -50.0
    assert metric["direction"] == "down"


# ---------------------------------------------------------------------------
# Occupancy
# ---------------------------------------------------------------------------
def test_occupancy_is_seats_over_capacity():
    start, end = day_window(0)
    today = timezone.localdate()
    lesson_a = build_lesson(on_date=today, capacity=8)
    lesson_b = build_lesson(on_date=today, capacity=8)

    build_booking(lesson=lesson_a, participants=4, booked_at=end - timedelta(hours=1))
    build_booking(lesson=lesson_b, participants=2, booked_at=end - timedelta(hours=1))
    # A cancelled booking released its seats and must not count.
    build_booking(
        lesson=lesson_b,
        participants=6,
        status=BookingStatus.CANCELLED,
        booked_at=end - timedelta(hours=1),
    )

    metric = services.lesson_occupancy(start, end)
    assert metric["capacity"] == 16
    assert metric["seats"] == 6
    assert metric["current"] == 37.5
    assert metric["empty_seats"] == 10


def test_a_cancelled_lesson_never_offered_its_seats():
    start, end = day_window(0)
    today = timezone.localdate()
    build_lesson(on_date=today, capacity=8)
    build_lesson(on_date=today, capacity=8, status=LessonStatus.CANCELLED)

    metric = services.lesson_occupancy(start, end)
    assert metric["capacity"] == 8


def test_occupancy_breaks_down_by_lesson_type():
    start, end = day_window(0)
    today = timezone.localdate()
    beginners = build_lesson_type(name="Beginner group")
    lesson = build_lesson(on_date=today, capacity=10, lesson_type=beginners)
    build_booking(lesson=lesson, participants=5, booked_at=end - timedelta(hours=1))

    rows = services.lesson_occupancy(start, end)["by_type"]
    assert {row["label"] for row in rows} == {"Beginner group"}
    assert rows[0]["rate"] == 50.0


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def test_customers_split_into_first_time_and_returning():
    start, end = day_window(6)
    loyal = build_customer()
    student = build_student(customer=loyal)
    # An older booking makes this customer "returning" in the current window.
    build_booking(
        customer=loyal, student=student, booked_at=start - timedelta(days=5), paid=Decimal("10.00")
    )
    build_booking(
        customer=loyal, student=student, booked_at=end - timedelta(hours=1), paid=Decimal("10.00")
    )
    build_booking(booked_at=end - timedelta(hours=1), paid=Decimal("10.00"))

    metric = services.customer_metrics(start, end)
    assert metric["current"] == 2  # two distinct customers were active
    assert metric["returning"] == 1
    assert metric["first_time"] == 1
    assert metric["repeat_customers"] == 1
    assert metric["repeat_rate"] == 50.0
    assert metric["new_vs_returning"][0]["count"] == 1
    assert metric["new_vs_returning"][1]["count"] == 1


# ---------------------------------------------------------------------------
# Instructors
# ---------------------------------------------------------------------------
def test_instructor_metrics_rank_by_lessons_delivered():
    start, end = day_window(0)
    today = timezone.localdate()
    busy = build_instructor()
    quiet = build_instructor()
    build_lesson(on_date=today, instructor=busy, capacity=8)
    build_lesson(on_date=today, instructor=busy, capacity=6)
    build_lesson(on_date=today, instructor=quiet, capacity=4)

    metric = services.instructor_metrics(start, end)
    assert metric["current"] == 3
    assert metric["active_instructors"] == 2
    assert metric["by_instructor"][0]["lessons"] == 2
    assert metric["by_instructor"][0]["capacity"] == 14
    assert metric["lessons_per_instructor"] == 1.5


# ---------------------------------------------------------------------------
# Equipment & rentals
# ---------------------------------------------------------------------------
def test_equipment_utilisation_counts_only_hours_inside_the_window():
    end = timezone.now()
    start = end - timedelta(hours=6)
    board = build_equipment()
    build_equipment()  # a second rentable item that never leaves the rack

    rental = build_rental(start_at=end - timedelta(hours=5), hours=4)
    build_rental_item(rental, board)

    metric = services.equipment_utilisation(start, end)
    assert metric["fleet_size"] == 2
    assert metric["used_hours"] == pytest.approx(4.0, abs=0.05)
    assert metric["items_used"] == 1
    assert metric["items_idle"] == 1
    assert 0 < metric["current"] <= 100


def test_a_long_hire_only_contributes_the_part_inside_the_window():
    end = timezone.now()
    start = end - timedelta(hours=2)
    board = build_equipment()
    rental = build_rental(start_at=end - timedelta(hours=20), hours=20)
    build_rental_item(rental, board)

    metric = services.equipment_utilisation(start, end)
    assert metric["used_hours"] == pytest.approx(2.0, abs=0.05)


def test_non_rentable_stock_is_outside_the_utilisation_denominator():
    end = timezone.now()
    start = end - timedelta(hours=6)
    build_equipment(rentable=True)
    build_equipment(rentable=False)
    assert services.equipment_utilisation(start, end)["fleet_size"] == 1


def test_rental_metrics_report_volume_income_and_duration():
    end = timezone.now()
    start = end - timedelta(hours=12)
    build_rental(start_at=end - timedelta(hours=5), hours=4, total=Decimal("40.00"))
    build_rental(start_at=end - timedelta(hours=9), hours=8, total=Decimal("60.00"))

    metric = services.rental_metrics(start, end)
    assert metric["current"] == 2
    assert metric["income"] == Decimal("100.00")
    assert metric["average_hours"] == pytest.approx(6.0, abs=0.05)


def test_late_returns_are_counted():
    end = timezone.now()
    start = end - timedelta(hours=12)
    rental = build_rental(start_at=end - timedelta(hours=6), hours=2)
    type(rental).objects.filter(pk=rental.pk).update(returned_at=end)

    metric = services.rental_metrics(start, end)
    assert metric["late_returns"] == 1
    assert metric["late_rate"] == 100.0


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------
def test_level_distribution_covers_students_active_in_the_period():
    start, end = day_window(6)
    customer = build_customer()
    student = build_student(customer=customer, level=SurfLevel.INTERMEDIATE)
    build_booking(customer=customer, student=student, booked_at=end - timedelta(hours=1))

    metric = services.student_level_distribution(start, end)
    assert metric["scope"] == "period"
    assert metric["current"] == 1
    row = next(r for r in metric["distribution"] if r["value"] == SurfLevel.INTERMEDIATE)
    assert row["count"] == 1
    assert row["share"] == 100.0


def test_level_distribution_falls_back_to_the_roster_and_says_so():
    start, end = day_window(6)
    build_student(level=SurfLevel.BEGINNER)
    metric = services.student_level_distribution(start, end)
    assert metric["scope"] == "all_active"
    assert metric["current"] == 1


def test_channel_mix_attributes_every_booking():
    start, end = day_window(6)
    build_booking(booked_at=end - timedelta(hours=1), source=BookingSource.WEBSITE)
    build_booking(booked_at=end - timedelta(hours=1), source=BookingSource.WEBSITE)
    build_booking(booked_at=end - timedelta(hours=1), source=BookingSource.PHONE)

    metric = services.channel_mix(start, end)
    assert metric["current"] == 3
    leading = metric["channels"][0]
    assert leading["value"] == BookingSource.WEBSITE
    assert leading["count"] == 2
    assert leading["share"] == pytest.approx(66.7, abs=0.1)
    assert len(metric["channels"]) == len(BookingSource.choices)


def test_busiest_hours_reads_the_lesson_start_time():
    start, end = day_window(0)
    today = timezone.localdate()
    lesson = build_lesson(on_date=today, hour=14, capacity=8)
    build_booking(lesson=lesson, participants=3, booked_at=end - timedelta(hours=1))

    metric = services.busiest_hours(start, end)
    assert len(metric["hours"]) == 24
    assert metric["hours"][14]["lessons"] == 1
    assert metric["hours"][14]["seats"] == 3
    assert metric["peak_hour"] == "14:00"
    assert metric["change_pct"] is None  # an hour profile has no predecessor


def test_busiest_weekdays_is_monday_first():
    start, end = day_window(6)
    today = timezone.localdate()
    lesson = build_lesson(on_date=today, capacity=8)
    build_booking(lesson=lesson, participants=2, booked_at=end - timedelta(hours=1))

    metric = services.busiest_weekdays(start, end)
    assert [row["weekday"] for row in metric["weekdays"]] == [1, 2, 3, 4, 5, 6, 7]
    row = metric["weekdays"][today.weekday()]
    assert row["lessons"] == 1
    assert row["seats"] == 2


# ---------------------------------------------------------------------------
# Forecast, summary, export
# ---------------------------------------------------------------------------
def test_revenue_forecast_on_an_empty_school_is_honest():
    result = services.revenue_forecast(days=30)
    assert result["horizon_days"] == 30
    assert result["low_confidence"] is True
    assert result["warning"] is not None


def test_revenue_forecast_horizon_is_bounded():
    assert services.revenue_forecast(days="not a number")["horizon_days"] == 30
    assert services.revenue_forecast(days=100_000)["horizon_days"] <= 365


def test_statistical_summary_describes_the_chosen_series():
    start, end = day_window(6)
    metrics = services.dashboard_metrics(start, end)
    summary = services.statistical_summary(metrics, "bookings")
    assert summary["metric"] == "bookings"
    assert summary["n"] == 7
    assert "trend" in summary


def test_statistical_summary_falls_back_to_revenue_for_an_unknown_key():
    start, end = day_window(6)
    metrics = services.dashboard_metrics(start, end)
    assert services.statistical_summary(metrics, "nonsense")["metric"] == "revenue"


def test_statistical_summary_does_not_correlate_revenue_with_itself():
    start, end = day_window(6)
    metrics = services.dashboard_metrics(start, end)
    assert services.statistical_summary(metrics, "revenue")["revenue_correlation"] is None


def test_ai_payload_is_numeric_and_small():
    start, end = day_window(6)
    metrics = services.dashboard_metrics(start, end)
    forecast = services.revenue_forecast(days=7)
    payload = services.ai_narrative_payload(metrics, forecast, "Last 7 days")
    assert payload["period"] == "Last 7 days"
    assert set(payload["revenue"]) == {"current", "previous", "change_pct", "unit"}
    assert payload["forecast"]["reliable"] is False


def test_export_rows_start_with_a_header_and_cover_every_section():
    start, end = day_window(6)
    metrics = services.dashboard_metrics(start, end)
    forecast = services.revenue_forecast(days=7)
    # Pin the language so the assertion tests the source strings, not whichever
    # catalogue happens to be installed.
    with translation.override("en"):
        rows = services.export_rows(metrics, forecast, "Last 7 days")
    assert all(len(row) == 4 for row in rows)
    sections = {row[0] for row in rows}
    assert "Bookings" in sections
    assert "Customers" in sections
    assert "Revenue forecast" in sections
    assert "Revenue over time" in sections
