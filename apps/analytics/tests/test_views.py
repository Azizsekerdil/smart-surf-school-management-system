from __future__ import annotations

import csv
import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role

from .factories import build_booking, build_lesson

pytestmark = pytest.mark.django_db
User = get_user_model()

DASHBOARD = "analytics:dashboard"
EXPORT = "analytics:export"
SUMMARY = "analytics:summary"
NARRATIVE = "analytics:narrative"


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="an-manager",
        email="an-manager@example.com",
        password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def instructor(db):
    """A surf instructor holds no ``analytics.*`` capability at all."""
    return User.objects.create_user(
        username="an-coach",
        email="an-coach@example.com",
        password="pw-test-12345",
        role=Role.SURF_INSTRUCTOR,
    )


@pytest.fixture
def head_instructor(db):
    """Head instructors may view and export analytics, but nothing more."""
    return User.objects.create_user(
        username="an-head",
        email="an-head@example.com",
        password="pw-test-12345",
        role=Role.HEAD_INSTRUCTOR,
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        username="an-guest",
        email="an-guest@example.com",
        password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_dashboard_requires_authentication(client):
    response = client.get(reverse(DASHBOARD))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_a_surf_instructor_may_not_see_analytics(client, instructor):
    client.force_login(instructor)
    assert client.get(reverse(DASHBOARD)).status_code == 403


def test_a_customer_may_not_see_analytics(client, customer):
    client.force_login(customer)
    assert client.get(reverse(DASHBOARD)).status_code == 403


def test_export_requires_the_export_capability(client, instructor):
    client.force_login(instructor)
    assert client.get(reverse(EXPORT)).status_code == 403


def test_head_instructor_may_view_and_export(client, head_instructor):
    client.force_login(head_instructor)
    assert client.get(reverse(DASHBOARD)).status_code == 200
    assert client.get(reverse(EXPORT)).status_code == 200


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_renders_on_a_completely_empty_school(client, manager):
    """The most likely state on day one must not produce a 500."""
    client.force_login(manager)
    response = client.get(reverse(DASHBOARD))
    assert response.status_code == 200
    assert set(response.context["metrics"]) >= {"revenue", "bookings", "occupancy"}
    assert response.context["forecast"]["low_confidence"] is True


def test_dashboard_renders_with_real_data(client, manager):
    today = timezone.localdate()
    lesson = build_lesson(on_date=today, capacity=8)
    build_booking(lesson=lesson, participants=3, paid=Decimal("75.00"))

    client.force_login(manager)
    response = client.get(reverse(DASHBOARD), {"range": "30"})
    assert response.status_code == 200
    assert response.context["metrics"]["occupancy"]["seats"] == 3
    assert response.context["chart_data"]["hours"]["labels"][0] == "00:00"


def test_dashboard_honours_the_custom_range(client, manager):
    client.force_login(manager)
    today = timezone.localdate()
    response = client.get(
        reverse(DASHBOARD),
        {
            "range": "custom",
            "start": (today - timedelta(days=3)).isoformat(),
            "end": today.isoformat(),
        },
    )
    assert response.status_code == 200
    assert len(response.context["metrics"]["revenue"]["series"]) == 4


def test_an_invalid_metric_selection_falls_back_to_revenue(client, manager):
    client.force_login(manager)
    response = client.get(reverse(DASHBOARD), {"metric": "../../etc/passwd"})
    assert response.status_code == 200
    assert response.context["selected_metric"] == "revenue"


def test_an_invalid_horizon_falls_back_to_the_default(client, manager):
    client.force_login(manager)
    response = client.get(reverse(DASHBOARD), {"horizon": "9999"})
    assert response.status_code == 200
    assert response.context["horizon"] == 30


def test_chart_data_carries_the_moving_average_overlay(client, manager):
    client.force_login(manager)
    response = client.get(reverse(DASHBOARD), {"range": "30"})
    revenue = response.context["chart_data"]["revenue"]
    assert len(revenue["moving_average"]) == len(revenue["values"])
    assert revenue["moving_average"][0] is None


# ---------------------------------------------------------------------------
# Statistical summary partial
# ---------------------------------------------------------------------------
def test_summary_partial_renders_for_a_chosen_series(client, manager):
    client.force_login(manager)
    response = client.get(reverse(SUMMARY), {"range": "30", "metric": "bookings"})
    assert response.status_code == 200
    assert response.context["selected_metric"] == "bookings"


def test_summary_partial_rejects_an_unknown_series(client, manager):
    client.force_login(manager)
    response = client.get(reverse(SUMMARY), {"metric": "salaries"})
    assert response.context["selected_metric"] == "revenue"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def test_export_returns_a_csv_attachment(client, manager):
    client.force_login(manager)
    response = client.get(reverse(EXPORT), {"range": "7"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment; filename=" in response["Content-Disposition"]
    body = response.content.decode("utf-8-sig")
    assert body.count("\n") > 5


def test_export_reflects_the_data_on_the_screen(client, manager):
    """The spreadsheet and the dashboard must never tell two different stories."""
    build_booking(paid=Decimal("125.00"))
    client.force_login(manager)
    dashboard = client.get(reverse(DASHBOARD), {"range": "7"})
    screen_series = dashboard.context["metrics"]["revenue"]["series"]

    response = client.get(reverse(EXPORT), {"range": "7"})
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    cells = {cell for row in rows for cell in row}

    assert {point["date"] for point in screen_series} <= cells


# ---------------------------------------------------------------------------
# AI narrative — never load-bearing
# ---------------------------------------------------------------------------
def test_narrative_is_silent_when_no_provider_answers(client, manager):
    """No configured provider means no section — not an error banner."""
    client.force_login(manager)
    with mock.patch("apps.ai.services.summarise_for_dashboard", return_value=("", False)):
        response = client.get(reverse(NARRATIVE))
    assert response.status_code == 200
    assert response.content == b""


def test_narrative_is_skipped_for_a_user_without_ai_access(client, manager):
    client.force_login(manager)
    with mock.patch.object(
        type(manager), "has_capability", lambda self, cap: cap != "ai.view"
    ), mock.patch("apps.ai.services.summarise_for_dashboard") as narrator:
        response = client.get(reverse(NARRATIVE))
    assert response.content == b""
    assert not narrator.called


def test_narrative_is_silent_when_the_provider_fails(client, manager):
    client.force_login(manager)
    with mock.patch(
        "apps.ai.services.summarise_for_dashboard", side_effect=RuntimeError("no provider")
    ):
        response = client.get(reverse(NARRATIVE))
    assert response.status_code == 200
    assert response.content == b""


def test_narrative_renders_inside_the_ai_surface_with_its_chip(client, manager):
    client.force_login(manager)
    with mock.patch(
        "apps.ai.services.summarise_for_dashboard",
        return_value=("Revenue held steady while occupancy improved.", True),
    ):
        response = client.get(reverse(NARRATIVE))
    body = response.content.decode()
    assert "ai-surface" in body
    assert "ai-chip" in body
    assert "Revenue held steady" in body


def test_narrative_never_receives_a_figure_it_could_invent(client, manager):
    """The model is handed finished numbers, never a queryset or raw rows."""
    client.force_login(manager)
    with mock.patch(
        "apps.ai.services.summarise_for_dashboard", return_value=("", False)
    ) as narrator:
        client.get(reverse(NARRATIVE), {"range": "7"})

    assert narrator.called
    payload = narrator.call_args[0][2]
    assert isinstance(payload, dict)
    assert "revenue" in payload and "forecast" in payload
    assert payload["forecast"]["reliable"] is False
