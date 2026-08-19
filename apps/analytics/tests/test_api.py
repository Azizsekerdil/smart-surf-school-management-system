from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.analytics.models import MetricSnapshot

from .factories import MetricSnapshotFactory, build_booking

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="api-an-manager",
        email="api-an-manager@example.com",
        password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def finance(db):
    """Finance may read analytics but holds no analytics.add."""
    return User.objects.create_user(
        username="api-an-finance",
        email="api-an-finance@example.com",
        password="pw-test-12345",
        role=Role.FINANCE,
    )


@pytest.fixture
def instructor(db):
    return User.objects.create_user(
        username="api-an-coach",
        email="api-an-coach@example.com",
        password="pw-test-12345",
        role=Role.SURF_INSTRUCTOR,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_anonymous_access_is_rejected(api):
    assert api.get(reverse("metricsnapshot-list")).status_code in (401, 403)
    assert api.get(reverse("analytics-list")).status_code in (401, 403)


def test_a_surf_instructor_holds_no_analytics_capability(api, instructor):
    api.force_authenticate(instructor)
    assert api.get(reverse("metricsnapshot-list")).status_code == 403
    assert api.get(reverse("analytics-list")).status_code == 403


def test_finance_may_read_but_not_create_snapshots(api, finance):
    MetricSnapshotFactory()
    api.force_authenticate(finance)
    assert api.get(reverse("metricsnapshot-list")).status_code == 200
    response = api.post(
        reverse("metricsnapshot-list"),
        {
            "metric_key": "revenue.total",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "granularity": MetricSnapshot.Granularity.MONTH,
            "value": "100.0000",
        },
        format="json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------
def test_manager_can_create_and_read_a_snapshot(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        reverse("metricsnapshot-list"),
        {
            "metric_key": "revenue.total",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "granularity": MetricSnapshot.Granularity.MONTH,
            "value": "1250.5000",
            "count": 40,
            "dimensions": {"channel": "website"},
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["span_days"] == 31
    assert MetricSnapshot.objects.get(metric_key="revenue.total").value == Decimal("1250.5000")


def test_a_backwards_period_is_rejected_by_the_serializer(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        reverse("metricsnapshot-list"),
        {
            "metric_key": "revenue.total",
            "period_start": "2026-02-01",
            "period_end": "2026-01-01",
            "value": "1.0000",
        },
        format="json",
    )
    assert response.status_code == 400
    # The project wraps every API error in a single envelope.
    assert response.data["error"]["type"] == "validation_error"
    assert "period_end" in response.data["error"]["detail"]


def test_snapshots_can_be_filtered_by_metric_key(api, manager):
    MetricSnapshotFactory(metric_key="revenue.total")
    MetricSnapshotFactory(metric_key="bookings.count")
    api.force_authenticate(manager)
    response = api.get(reverse("metricsnapshot-list"), {"metric_key": "revenue.total"})
    assert response.status_code == 200
    assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# Live metrics
# ---------------------------------------------------------------------------
def test_metrics_endpoint_returns_every_headline(api, manager):
    build_booking(paid=Decimal("60.00"))
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-list"), {"range": "30"})
    assert response.status_code == 200
    assert "period" in response.data
    metrics = response.data["metrics"]
    assert metrics["bookings"]["current"] == 1
    assert set(metrics) >= {"revenue", "bookings", "occupancy", "customers"}


def test_metrics_payload_is_json_serialisable(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-list"))
    # ``.json()`` fails loudly on a stray Decimal or lazy translation proxy.
    assert response.json()["metrics"]["revenue"]["unit"] == "money"


def test_forecast_endpoint_always_states_its_confidence(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-forecast"), {"days": 14})
    assert response.status_code == 200
    assert response.data["horizon_days"] == 14
    assert response.data["confidence"] in {"high", "medium", "low", "none"}
    assert response.data["low_confidence"] is True
    assert response.data["warning"]


def test_forecast_endpoint_survives_a_junk_horizon(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-forecast"), {"days": "soon"})
    assert response.status_code == 200
    assert response.data["horizon_days"] == 30


def test_summary_endpoint_falls_back_to_revenue(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-summary"), {"metric": "payroll"})
    assert response.status_code == 200
    assert response.data["summary"]["metric"] == "revenue"


def test_capabilities_endpoint_lists_the_engine_limits(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("analytics-capabilities"))
    assert response.status_code == 200
    assert response.data["max_forecast_periods"] == 365
    assert "linear" in response.data["forecast_methods"]
    assert any(row["key"] == "revenue" for row in response.data["metrics"])
