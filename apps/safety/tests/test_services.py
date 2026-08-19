"""Service rules — above all: an unconfirmed AI suggestion changes nothing."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.constants import Role
from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import GenericStatus, Severity, SurfLevel
from apps.safety import services
from apps.safety.models import SafetyIncident, StudentRestriction

from .factories import (
    AISuggestedWarningFactory,
    BlockingRestrictionFactory,
    EquipmentFactory,
    EquipmentSafetyCheckFactory,
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
def reporter(db):
    return SafetyUserFactory(username="reporter", role=Role.HEAD_INSTRUCTOR)


@pytest.fixture
def instructor(db):
    """Holds ``safety.view``/``safety.add`` but never ``safety.approve``."""
    return SafetyUserFactory(username="coach", role=Role.SURF_INSTRUCTOR)


# ---------------------------------------------------------------------------
# report_incident
# ---------------------------------------------------------------------------
def test_report_incident_writes_a_safety_audit_entry(reporter):
    spot = SurfSpotFactory()
    incident = services.report_incident(
        occurred_at=timezone.now() - timedelta(minutes=20),
        incident_type=SafetyIncident.IncidentType.RESCUE,
        severity=Severity.MEDIUM,
        description="Student separated from the board outside the flags.",
        immediate_action="Board rescue by the water-safety instructor.",
        spot=spot,
        reported_by=reporter,
        conditions_at_time={"wave_height_m": 1.1},
    )

    assert incident.incident_code.startswith("INC")
    assert incident.reported_by == reporter

    entry = AuditLog.objects.filter(action=AuditAction.SAFETY_INCIDENT).first()
    assert entry is not None
    assert entry.is_sensitive is True
    assert incident.incident_code in entry.description


def test_report_incident_notifies_managers(reporter):
    from apps.notifications.models import Notification

    manager = SafetyUserFactory(username="duty-manager", role=Role.MANAGER)
    services.report_incident(
        occurred_at=timezone.now(),
        incident_type=SafetyIncident.IncidentType.INJURY,
        severity=Severity.HIGH,
        description="Fin cut to the shin.",
        immediate_action="Dressing applied.",
        reported_by=reporter,
    )
    assert Notification.objects.filter(recipient=manager).exists()


def test_review_incident_requires_the_approve_capability(instructor):
    incident = SeriousIncidentFactory()
    with pytest.raises(PermissionDenied):
        services.review_incident(
            incident,
            user=instructor,
            root_cause="Board too close to the shorebreak.",
            corrective_action="Move the teaching zone 30 m north.",
            status=GenericStatus.RESOLVED,
        )


def test_review_incident_records_the_named_reviewer(reporter):
    incident = SeriousIncidentFactory()
    services.review_incident(
        incident,
        user=reporter,
        root_cause="Group drifted with the longshore current.",
        corrective_action="Reposition every 10 minutes against a fixed marker.",
        status=GenericStatus.RESOLVED,
    )
    incident.refresh_from_db()
    assert incident.reviewed_by == reporter
    assert incident.reviewed_at is not None
    assert incident.status == GenericStatus.RESOLVED
    assert incident.is_open is False


def test_days_since_last_incident():
    assert services.days_since_last_incident() is None
    SafetyIncidentFactory(occurred_at=timezone.now() - timedelta(days=12))
    assert services.days_since_last_incident() == 12


# ---------------------------------------------------------------------------
# Restrictions
# ---------------------------------------------------------------------------
def test_active_restrictions_excludes_expired_and_future():
    student = StudentFactory()
    current = StudentRestrictionFactory(student=student)
    StudentRestrictionFactory(
        student=student,
        starts_on=timezone.localdate() - timedelta(days=30),
        ends_on=timezone.localdate() - timedelta(days=1),
    )
    StudentRestrictionFactory(student=student, starts_on=timezone.localdate() + timedelta(days=3))

    active = list(services.active_restrictions_for(student))
    assert active == [current]


def test_check_student_can_surf_blocks_on_cannot_surf():
    student = StudentFactory()
    BlockingRestrictionFactory(student=student)

    may_surf, reasons = services.check_student_can_surf(student)
    assert may_surf is False
    assert any("must not enter the water" in reason for reason in reasons)


def test_check_student_can_surf_blocks_when_conditions_exceed_the_limit():
    student = StudentFactory()
    StudentRestrictionFactory(student=student, max_wave_height_m=1.0)

    may_surf, reasons = services.check_student_can_surf(student, {"wave_height_m": 1.8})
    assert may_surf is False
    assert any("1.8" in reason for reason in reasons)


def test_check_student_can_surf_allows_within_the_limit():
    student = StudentFactory()
    StudentRestrictionFactory(student=student, max_wave_height_m=1.5)

    may_surf, reasons = services.check_student_can_surf(student, {"wave_height_m": 0.6})
    assert may_surf is True
    assert reasons == []


def test_unknown_conditions_warn_rather_than_silently_pass():
    student = StudentFactory()
    StudentRestrictionFactory(student=student, max_wave_height_m=1.0)

    verdict = services.evaluate_student(student, {"water_temp_c": 19})
    assert verdict.ok is True
    assert verdict.blocking == []
    assert any("confirm" in warning.lower() for warning in verdict.warnings)


def test_supervision_requirement_is_a_warning_not_a_block():
    student = StudentFactory()
    StudentRestrictionFactory(
        student=student,
        max_wave_height_m=None,
        requires_supervision=True,
        description="First open-water session after a panic incident.",
    )
    may_surf, reasons = services.check_student_can_surf(student)
    assert may_surf is True
    assert any("supervision" in reason.lower() for reason in reasons)


def test_deactivate_restriction_keeps_the_record(reporter):
    restriction = StudentRestrictionFactory()
    services.deactivate_restriction(restriction, user=reporter)
    restriction.refresh_from_db()
    assert restriction.is_active is False
    assert restriction.ends_on is not None
    assert StudentRestriction.all_objects.filter(pk=restriction.pk).exists()


# ---------------------------------------------------------------------------
# Warnings: the AI/human boundary
# ---------------------------------------------------------------------------
def test_unconfirmed_ai_suggestion_is_not_an_authoritative_warning():
    AISuggestedWarningFactory(severity=Severity.CRITICAL)
    assert services.authoritative_warnings().count() == 0
    assert services.pending_ai_warnings().count() == 1


def test_confirmed_ai_suggestion_becomes_authoritative(reporter):
    warning = AISuggestedWarningFactory(severity=Severity.CRITICAL)
    services.acknowledge_warning(warning, reporter)

    warning.refresh_from_db()
    assert warning.acknowledged_by == reporter
    assert warning.acknowledged_at is not None
    assert services.authoritative_warnings().count() == 1
    assert services.pending_ai_warnings().count() == 0


def test_acknowledging_requires_the_approve_capability(instructor):
    warning = AISuggestedWarningFactory()
    with pytest.raises(PermissionDenied):
        services.acknowledge_warning(warning, instructor)
    warning.refresh_from_db()
    assert warning.acknowledged_by_id is None


def test_acknowledging_is_idempotent(reporter):
    warning = AISuggestedWarningFactory()
    services.acknowledge_warning(warning, reporter)
    first = warning.acknowledged_at
    services.acknowledge_warning(warning, SafetyUserFactory(username="second"))
    warning.refresh_from_db()
    assert warning.acknowledged_by == reporter
    assert warning.acknowledged_at == first


def test_dismiss_warning_deactivates_it(reporter):
    warning = AISuggestedWarningFactory()
    services.dismiss_warning(warning, reporter)
    warning.refresh_from_db()
    assert warning.is_active is False
    assert services.pending_ai_warnings().count() == 0


def test_warnings_outside_their_window_are_not_in_force():
    WeatherWarningFactory(
        starts_at=timezone.now() + timedelta(hours=4),
        ends_at=timezone.now() + timedelta(hours=8),
    )
    assert services.authoritative_warnings().count() == 0


# ---------------------------------------------------------------------------
# is_spot_safe_now
# ---------------------------------------------------------------------------
def test_spot_check_ignores_an_unconfirmed_ai_warning():
    spot = SurfSpotFactory(lifeguard_on_duty=True)
    LifeguardAssignmentFactory(spot=spot, start_time=time(0, 1), end_time=time(23, 59))
    AISuggestedWarningFactory(spot=spot, severity=Severity.CRITICAL)

    safe, reasons = services.is_spot_safe_now(spot, SurfLevel.BEGINNER)
    assert safe is True
    # It is not silent about it either: staff are told a suggestion is waiting.
    assert any("awaiting staff confirmation" in reason for reason in reasons)


def test_spot_check_blocks_once_the_ai_warning_is_confirmed(reporter):
    spot = SurfSpotFactory(lifeguard_on_duty=True)
    LifeguardAssignmentFactory(spot=spot, start_time=time(0, 1), end_time=time(23, 59))
    warning = AISuggestedWarningFactory(spot=spot, severity=Severity.CRITICAL)
    services.acknowledge_warning(warning, reporter)

    safe, reasons = services.is_spot_safe_now(spot, SurfLevel.BEGINNER)
    assert safe is False
    assert any(warning.title in reason for reason in reasons)


def test_spot_check_blocks_on_a_high_manual_warning():
    spot = SurfSpotFactory()
    WeatherWarningFactory(spot=spot, severity=Severity.HIGH, title="Storm surge")
    safe, reasons = services.is_spot_safe_now(spot)
    assert safe is False
    assert any("Storm surge" in reason for reason in reasons)


def test_spot_check_blocks_on_a_critical_hazard():
    from apps.locations.tests.factories import SpotHazardFactory

    spot = SurfSpotFactory()
    SpotHazardFactory(spot=spot, severity=Severity.CRITICAL, name="Exposed reef shelf")
    safe, reasons = services.is_spot_safe_now(spot)
    assert safe is False
    assert any("Exposed reef shelf" in reason for reason in reasons)


def test_spot_check_blocks_on_an_open_critical_incident():
    spot = SurfSpotFactory()
    incident = SafetyIncidentFactory(
        spot=spot, severity=Severity.CRITICAL, status=GenericStatus.OPEN,
        occurred_at=timezone.now() - timedelta(hours=2),
    )
    safe, reasons = services.is_spot_safe_now(spot)
    assert safe is False
    assert any(incident.incident_code in reason for reason in reasons)


def test_spot_check_blocks_a_level_the_spot_does_not_accept():
    spot = SurfSpotFactory(
        min_level=SurfLevel.INTERMEDIATE, max_level=SurfLevel.COMPETITION
    )
    safe, reasons = services.is_spot_safe_now(spot, SurfLevel.FIRST_TIME)
    assert safe is False
    assert any(spot.name in reason for reason in reasons)


def test_spot_check_says_so_when_there_is_no_computed_score():
    spot = SurfSpotFactory()
    verdict = services.assess_spot(spot, SurfLevel.BEGINNER)
    assert any("no computed surf score" in warning.lower() for warning in verdict.warnings)


def test_spot_check_honours_an_injected_score():
    class FakeScore:
        safety_verdict = "NO_GO"
        score = 12
        computed_at = timezone.now()

        def suits_level(self, level):
            return False

    spot = SurfSpotFactory()
    safe, reasons = services.is_spot_safe_now(
        spot, SurfLevel.BEGINNER, surf_score=FakeScore()
    )
    assert safe is False
    assert any("NO_GO" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# Cover, checks and drills
# ---------------------------------------------------------------------------
def test_only_confirmed_shifts_count_as_cover():
    spot = SurfSpotFactory()
    LifeguardAssignmentFactory(
        spot=spot, is_confirmed=False, start_time=time(0, 1), end_time=time(23, 59)
    )
    assert services.lifeguard_cover(spot).count() == 0

    assignment = LifeguardAssignmentFactory(
        spot=spot, is_confirmed=False, start_time=time(0, 2), end_time=time(23, 58)
    )
    services.confirm_assignment(assignment, user=SafetyUserFactory(username="rota"))
    assert services.lifeguard_cover(spot).count() == 1


def test_roster_for_week_returns_seven_days():
    monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    LifeguardAssignmentFactory(date=monday)
    week = services.roster_for_week(monday)
    assert len(week["days"]) == 7
    assert week["days"][0]["date"] == monday
    assert week["total"] == 1


def test_only_the_latest_check_per_item_can_be_overdue():
    item = EquipmentFactory()
    EquipmentSafetyCheckFactory(
        equipment=item,
        checked_at=timezone.now() - timedelta(days=60),
        next_check_due=timezone.localdate() - timedelta(days=30),
    )
    assert services.overdue_equipment_checks().count() == 1

    EquipmentSafetyCheckFactory(
        equipment=item,
        checked_at=timezone.now() - timedelta(days=1),
        next_check_due=timezone.localdate() + timedelta(days=30),
    )
    assert services.overdue_equipment_checks().count() == 0


def test_overdue_drills_are_listed_first():
    overdue = EvacuationPlanFactory(next_drill_due=timezone.localdate() - timedelta(days=5))
    soon = EvacuationPlanFactory(next_drill_due=timezone.localdate() + timedelta(days=10))
    EvacuationPlanFactory(next_drill_due=timezone.localdate() + timedelta(days=90))

    plans = list(services.upcoming_drills())
    assert plans == [overdue, soon]
    assert services.overdue_drills().count() == 1


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_stats_separate_confirmed_from_ai_suggested():
    start = timezone.now() - timedelta(days=30)
    end = timezone.now()

    SafetyIncidentFactory(occurred_at=timezone.now() - timedelta(days=2))
    SeriousIncidentFactory(occurred_at=timezone.now() - timedelta(days=1))
    WeatherWarningFactory()
    AISuggestedWarningFactory()

    stats = services.safety_dashboard_stats(start, end)
    assert stats["total"] == 2
    assert stats["serious"] == 1
    assert stats["active_warnings"] == 1
    assert stats["pending_ai_warnings"] == 1
    assert stats["open_incidents"] == 2
    assert len(stats["trend"]["labels"]) == len(stats["trend"]["counts"])
    assert sum(stats["trend"]["counts"]) == 2
