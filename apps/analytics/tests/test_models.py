from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.analytics.models import MetricSnapshot

from .factories import MetricSnapshotFactory

pytestmark = pytest.mark.django_db


def test_str_names_the_metric_and_its_window():
    today = timezone.localdate()
    snapshot = MetricSnapshotFactory(
        metric_key="revenue.total", period_start=today, period_end=today
    )
    assert "revenue.total" in str(snapshot)
    assert today.strftime("%Y-%m-%d") in str(snapshot)


def test_span_days_counts_both_ends():
    today = timezone.localdate()
    snapshot = MetricSnapshotFactory(
        period_start=today - timedelta(days=6), period_end=today
    )
    assert snapshot.span_days == 7


def test_value_as_float_is_safe_for_charting():
    snapshot = MetricSnapshotFactory(value=Decimal("1234.5678"))
    assert snapshot.value_as_float == pytest.approx(1234.5678)


def test_a_window_may_only_be_measured_once_per_metric():
    today = timezone.localdate()
    MetricSnapshotFactory(
        metric_key="revenue.total",
        period_start=today,
        period_end=today,
        granularity=MetricSnapshot.Granularity.DAY,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricSnapshot.objects.create(
            metric_key="revenue.total",
            period_start=today,
            period_end=today,
            granularity=MetricSnapshot.Granularity.DAY,
            value=Decimal("1.0000"),
        )


def test_the_same_window_at_another_granularity_is_a_different_row():
    today = timezone.localdate()
    MetricSnapshotFactory(
        metric_key="revenue.total",
        period_start=today,
        period_end=today,
        granularity=MetricSnapshot.Granularity.DAY,
    )
    MetricSnapshot.objects.create(
        metric_key="revenue.total",
        period_start=today,
        period_end=today,
        granularity=MetricSnapshot.Granularity.MONTH,
        value=Decimal("2.0000"),
    )
    assert MetricSnapshot.objects.filter(metric_key="revenue.total").count() == 2


def test_a_backwards_period_is_rejected():
    today = timezone.localdate()
    snapshot = MetricSnapshot(
        metric_key="revenue.total",
        period_start=today,
        period_end=today - timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        snapshot.clean()


def test_record_is_idempotent():
    """Re-running the nightly computation must refresh, never duplicate."""
    today = timezone.localdate()
    first = MetricSnapshot.record("bookings.count", today, today, 12, count=12)
    second = MetricSnapshot.record("bookings.count", today, today, 19, count=19)

    assert first.pk == second.pk
    assert MetricSnapshot.objects.filter(metric_key="bookings.count").count() == 1
    second.refresh_from_db()
    assert second.value == Decimal("19.0000")
    assert second.count == 19


def test_record_quantises_the_value_and_floors_the_count():
    today = timezone.localdate()
    snapshot = MetricSnapshot.record(
        "occupancy.rate", today, today, "87.123456", count=-5
    )
    assert snapshot.value == Decimal("87.1235")
    assert snapshot.count == 0


def test_record_stores_dimensions():
    today = timezone.localdate()
    snapshot = MetricSnapshot.record(
        "revenue.total",
        today,
        today,
        Decimal("100.00"),
        granularity=MetricSnapshot.Granularity.MONTH,
        dimensions={"channel": "website"},
    )
    snapshot.refresh_from_db()
    assert snapshot.dimensions == {"channel": "website"}
    assert snapshot.granularity == MetricSnapshot.Granularity.MONTH


def test_default_ordering_is_newest_period_first():
    today = timezone.localdate()
    MetricSnapshotFactory(metric_key="a", period_start=today - timedelta(days=5), period_end=today)
    MetricSnapshotFactory(metric_key="b", period_start=today, period_end=today)
    keys = list(MetricSnapshot.objects.values_list("metric_key", flat=True))
    assert keys[0] == "b"
