"""REST API: capability enforcement and the AI sign-off contract."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import GenericStatus, Severity, SurfLevel

from .factories import (
    AISuggestedWarningFactory,
    BlockingRestrictionFactory,
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
def api():
    return APIClient()


@pytest.fixture
def manager(db):
    return SafetyUserFactory(username="api-manager", role=Role.MANAGER)


@pytest.fixture
def instructor(db):
    return SafetyUserFactory(username="api-coach", role=Role.SURF_INSTRUCTOR)


@pytest.fixture
def customer(db):
    return SafetyUserFactory(username="api-guest", role=Role.CUSTOMER)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def test_anonymous_access_is_refused(api):
    response = api.get("/api/v1/safety-incidents/")
    assert response.status_code in (401, 403)


def test_customer_has_no_safety_access(api, customer):
    api.force_authenticate(customer)
    assert api.get("/api/v1/safety-incidents/").status_code == 403


def test_manager_lists_incidents(api, manager):
    SafetyIncidentFactory()
    api.force_authenticate(manager)
    response = api.get("/api/v1/safety-incidents/")
    assert response.status_code == 200
    payload = response.json()
    results = payload["results"] if isinstance(payload, dict) else payload
    assert len(results) == 1
    assert results[0]["incident_code"].startswith("INC")


# ---------------------------------------------------------------------------
# Warnings: the AI contract
# ---------------------------------------------------------------------------
def test_authoritative_endpoint_hides_unconfirmed_ai_suggestions(api, manager):
    WeatherWarningFactory(title="Manual gale")
    AISuggestedWarningFactory(title="Model suggestion")

    api.force_authenticate(manager)
    authoritative = api.get("/api/v1/weather-warnings/authoritative/").json()
    pending = api.get("/api/v1/weather-warnings/pending/").json()

    assert [row["title"] for row in authoritative] == ["Manual gale"]
    assert [row["title"] for row in pending] == ["Model suggestion"]
    assert pending[0]["is_authoritative"] is False
    assert pending[0]["awaiting_confirmation"] is True
    assert "AI Recommendation" in pending[0]["display_title"]


def test_a_client_cannot_write_its_own_sign_off(api, instructor):
    """``acknowledged_by`` is read-only: no PATCH can fake a human decision."""
    warning = AISuggestedWarningFactory()
    api.force_authenticate(instructor)
    response = api.patch(
        f"/api/v1/weather-warnings/{warning.pk}/",
        {"acknowledged_by": instructor.pk, "acknowledged_at": timezone.now().isoformat()},
        format="json",
    )
    # Instructors hold no safety.change capability at all.
    assert response.status_code == 403
    warning.refresh_from_db()
    assert warning.acknowledged_by_id is None
    assert warning.is_authoritative is False


def test_acknowledge_action_requires_the_approve_capability(api, instructor):
    warning = AISuggestedWarningFactory()
    api.force_authenticate(instructor)
    response = api.post(f"/api/v1/weather-warnings/{warning.pk}/acknowledge/")
    assert response.status_code == 403
    warning.refresh_from_db()
    assert warning.acknowledged_by_id is None


def test_manager_acknowledges_through_the_api(api, manager):
    warning = AISuggestedWarningFactory()
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/weather-warnings/{warning.pk}/acknowledge/")
    assert response.status_code == 200
    assert response.json()["is_authoritative"] is True

    warning.refresh_from_db()
    assert warning.acknowledged_by == manager


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------
def test_spot_gate_reports_blockers(api, manager):
    spot = SurfSpotFactory()
    WeatherWarningFactory(spot=spot, severity=Severity.CRITICAL, title="Tsunami advisory")

    api.force_authenticate(manager)
    response = api.get("/api/v1/safety-gates/spot/", {"spot": spot.pk, "level": SurfLevel.BEGINNER})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert any("Tsunami advisory" in reason for reason in payload["blocking"])


def test_spot_gate_needs_a_spot(api, manager):
    api.force_authenticate(manager)
    response = api.get("/api/v1/safety-gates/spot/")
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "validation_error"


def test_student_gate_blocks_a_restricted_student(api, manager):
    student = StudentFactory()
    BlockingRestrictionFactory(student=student)

    api.force_authenticate(manager)
    response = api.get("/api/v1/safety-gates/student/", {"student": student.pk})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["blocking"]


def test_student_gate_uses_the_conditions_given(api, manager):
    student = StudentFactory()
    StudentRestrictionFactory(student=student, max_wave_height_m=1.0)

    api.force_authenticate(manager)
    over = api.get(
        "/api/v1/safety-gates/student/", {"student": student.pk, "wave_height_m": 2.0}
    ).json()
    under = api.get(
        "/api/v1/safety-gates/student/", {"student": student.pk, "wave_height_m": 0.5}
    ).json()

    assert over["ok"] is False
    assert under["ok"] is True


def test_student_gate_rejects_a_non_numeric_condition(api, manager):
    student = StudentFactory()
    api.force_authenticate(manager)
    response = api.get(
        "/api/v1/safety-gates/student/", {"student": student.pk, "wave_height_m": "big"}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Incidents & roster actions
# ---------------------------------------------------------------------------
def test_review_action_requires_approval_rights(api, instructor):
    incident = SeriousIncidentFactory()
    api.force_authenticate(instructor)
    response = api.post(
        f"/api/v1/safety-incidents/{incident.pk}/review/",
        {"root_cause": "x", "corrective_action": "y", "status": GenericStatus.RESOLVED},
        format="json",
    )
    assert response.status_code == 403


def test_review_action_closes_the_incident(api, manager):
    incident = SeriousIncidentFactory()
    api.force_authenticate(manager)
    response = api.post(
        f"/api/v1/safety-incidents/{incident.pk}/review/",
        {
            "root_cause": "Shorebreak dumping on a spring low.",
            "corrective_action": "No first-timers within two hours of low water.",
            "status": GenericStatus.RESOLVED,
        },
        format="json",
    )
    assert response.status_code == 200
    incident.refresh_from_db()
    assert incident.reviewed_by == manager
    assert incident.status == GenericStatus.RESOLVED


def test_days_since_last_endpoint(api, manager):
    SafetyIncidentFactory(occurred_at=timezone.now() - timedelta(days=4))
    api.force_authenticate(manager)
    response = api.get("/api/v1/safety-incidents/days-since-last/")
    assert response.status_code == 200
    assert response.json()["days_since_last_incident"] == 4


def test_confirm_shift_action(api, manager):
    assignment = LifeguardAssignmentFactory(is_confirmed=False)
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/lifeguard-assignments/{assignment.pk}/confirm/")
    assert response.status_code == 200
    assignment.refresh_from_db()
    assert assignment.is_confirmed is True


def test_creating_an_assignment_rejects_an_inverted_shift(api, manager):
    spot = SurfSpotFactory()
    guard = SafetyUserFactory(username="api-guard", role=Role.LIFEGUARD)
    api.force_authenticate(manager)
    response = api.post(
        "/api/v1/lifeguard-assignments/",
        {
            "spot": spot.pk,
            "lifeguard": guard.pk,
            "date": timezone.localdate().isoformat(),
            "start_time": "16:00",
            "end_time": "09:00",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "end_time" in response.json()["error"]["detail"]
