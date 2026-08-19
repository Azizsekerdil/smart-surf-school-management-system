"""HTML views: permissions, the screens render, and the AI split is visible."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import GenericStatus, Severity
from apps.safety.models import SafetyIncident, WeatherWarning

from .factories import (
    AISuggestedWarningFactory,
    EmergencyContactFactory,
    EvacuationPlanFactory,
    LifeguardAssignmentFactory,
    SafetyIncidentFactory,
    SafetyUserFactory,
    SeriousIncidentFactory,
    StudentFactory,
    StudentRestrictionFactory,
    SurfSpotFactory,
    WeatherWarningFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(db):
    return SafetyUserFactory(username="safety-manager", role=Role.MANAGER)


@pytest.fixture
def instructor(db):
    """``safety.view`` and ``safety.add``, but never ``safety.approve``."""
    return SafetyUserFactory(username="safety-coach", role=Role.SURF_INSTRUCTOR)


@pytest.fixture
def customer(db):
    return SafetyUserFactory(username="safety-guest", role=Role.CUSTOMER)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("safety:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_customer_cannot_open_the_safety_dashboard(client, customer):
    client.force_login(customer)
    assert client.get(reverse("safety:dashboard")).status_code == 403


def test_instructor_cannot_review_an_incident(client, instructor):
    incident = SafetyIncidentFactory()
    client.force_login(instructor)
    assert client.get(reverse("safety:incident_review", args=[incident.pk])).status_code == 403


def test_instructor_cannot_confirm_an_ai_warning(client, instructor):
    warning = AISuggestedWarningFactory()
    client.force_login(instructor)
    response = client.post(reverse("safety:warning_acknowledge", args=[warning.pk]))
    assert response.status_code == 403
    warning.refresh_from_db()
    assert warning.acknowledged_by_id is None


# ---------------------------------------------------------------------------
# Screens render
# ---------------------------------------------------------------------------
def test_dashboard_renders(client, manager):
    SafetyIncidentFactory()
    WeatherWarningFactory()
    LifeguardAssignmentFactory()
    client.force_login(manager)
    response = client.get(reverse("safety:dashboard"))
    assert response.status_code == 200
    assert "stats" in response.context


def test_dashboard_chart_payload_is_valid_json(client, manager):
    """The trend chart reads its data from a JSON block, not an attribute."""
    import json
    import re

    SafetyIncidentFactory()
    client.force_login(manager)
    body = client.get(reverse("safety:dashboard")).content.decode()

    assert 'id="incident-trend-chart"' in body
    match = re.search(
        r'<script type="application/json" id="incident-trend-data">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert len(payload["labels"]) == len(payload["counts"]) == len(payload["serious"])
    assert sum(payload["counts"]) == 1


def test_dashboard_keeps_ai_suggestions_out_of_the_confirmed_list(client, manager):
    confirmed = WeatherWarningFactory(title="Confirmed gale")
    suggested = AISuggestedWarningFactory(title="Model rip warning")

    client.force_login(manager)
    response = client.get(reverse("safety:dashboard"))

    assert list(response.context["confirmed_warnings"]) == [confirmed]
    assert list(response.context["pending_warnings"]) == [suggested]

    body = response.content.decode()
    assert "ai-surface" in body
    assert "AI Recommendation" in body
    assert "Awaiting staff confirmation" in body


def test_incident_list_and_detail_render(client, manager):
    incident = SafetyIncidentFactory()
    client.force_login(manager)
    assert client.get(reverse("safety:incident_list")).status_code == 200
    assert client.get(reverse("safety:incident_detail", args=[incident.pk])).status_code == 200


def test_incident_list_open_filter(client, manager):
    open_incident = SafetyIncidentFactory(status=GenericStatus.OPEN)
    SafetyIncidentFactory(
        status=GenericStatus.CLOSED, severity=Severity.LOW,
        occurred_at=timezone.now() - timedelta(days=5),
    )
    client.force_login(manager)
    response = client.get(reverse("safety:incident_list"), {"status": "open"})
    assert list(response.context["incidents"]) == [open_incident]


def test_roster_contacts_plans_checks_warnings_restrictions_render(client, manager):
    LifeguardAssignmentFactory()
    EmergencyContactFactory()
    plan = EvacuationPlanFactory()
    WeatherWarningFactory()
    StudentRestrictionFactory()

    client.force_login(manager)
    for name in (
        "safety:roster",
        "safety:contact_list",
        "safety:contact_card",
        "safety:plan_list",
        "safety:check_list",
        "safety:warning_list",
        "safety:restriction_list",
    ):
        assert client.get(reverse(name)).status_code == 200, name

    assert client.get(reverse("safety:plan_detail", args=[plan.pk])).status_code == 200


def test_create_forms_render(client, manager):
    client.force_login(manager)
    for name in (
        "safety:incident_create",
        "safety:roster_assign",
        "safety:contact_create",
        "safety:plan_create",
        "safety:check_create",
        "safety:warning_create",
        "safety:restriction_create",
    ):
        assert client.get(reverse(name)).status_code == 200, name


def test_spot_safety_panel_renders(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.get(reverse("safety:spot_panel", args=[spot.pk]))
    assert response.status_code == 200
    assert response.context["verdict"] is not None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def test_reporting_an_incident_creates_and_notifies(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.post(
        reverse("safety:incident_create"),
        {
            "occurred_at": (timezone.localtime() - timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            "incident_type": SafetyIncident.IncidentType.NEAR_MISS,
            "severity": Severity.LOW,
            "status": GenericStatus.OPEN,
            "spot": spot.pk,
            "description": "Student caught in the shorebreak, self-recovered.",
            "immediate_action": "Recalled the group and repositioned inside the flags.",
        },
    )
    assert response.status_code == 302
    incident = SafetyIncident.objects.get()
    assert incident.reported_by == manager
    assert incident.spot == spot


def test_manager_confirms_an_ai_warning(client, manager):
    warning = AISuggestedWarningFactory()
    client.force_login(manager)
    response = client.post(reverse("safety:warning_acknowledge", args=[warning.pk]))
    assert response.status_code == 302

    warning.refresh_from_db()
    assert warning.acknowledged_by == manager
    assert warning.is_authoritative is True


def test_manager_dismisses_an_ai_warning(client, manager):
    warning = AISuggestedWarningFactory()
    client.force_login(manager)
    client.post(reverse("safety:warning_dismiss", args=[warning.pk]))
    warning.refresh_from_db()
    assert warning.is_active is False


def test_reviewing_an_incident_closes_it(client, manager):
    incident = SeriousIncidentFactory()
    client.force_login(manager)
    response = client.post(
        reverse("safety:incident_review", args=[incident.pk]),
        {
            "root_cause": "Teaching zone too close to the rip feeder.",
            "corrective_action": "Move the zone 40 m north and re-brief the coaches.",
            "status": GenericStatus.RESOLVED,
            "follow_up_required": "",
        },
    )
    assert response.status_code == 302
    incident.refresh_from_db()
    assert incident.reviewed_by == manager
    assert incident.status == GenericStatus.RESOLVED


def test_confirming_a_shift_makes_it_count(client, manager):
    assignment = LifeguardAssignmentFactory(is_confirmed=False)
    client.force_login(manager)
    client.post(reverse("safety:assignment_confirm", args=[assignment.pk]))
    assignment.refresh_from_db()
    assert assignment.is_confirmed is True


def test_lifting_a_restriction_keeps_it_on_file(client, manager):
    restriction = StudentRestrictionFactory(student=StudentFactory())
    client.force_login(manager)
    client.post(reverse("safety:restriction_lift", args=[restriction.pk]))
    restriction.refresh_from_db()
    assert restriction.is_active is False


def test_the_warning_form_cannot_create_an_ai_suggestion(client, manager):
    client.force_login(manager)
    now = timezone.localtime()
    response = client.post(
        reverse("safety:warning_create"),
        {
            "title": "Fake model warning",
            "severity": Severity.HIGH,
            "source": WeatherWarning.Source.AI_SUGGESTED,
            "description": "Trying to smuggle in an AI-sourced warning.",
            "starts_at": now.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
            "is_active": "on",
        },
    )
    assert response.status_code == 200  # re-rendered with an error
    assert WeatherWarning.objects.count() == 0
