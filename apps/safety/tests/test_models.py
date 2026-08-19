"""Model-level rules: codes, validation and the AI/human separation."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import GenericStatus, Severity
from apps.safety.models import (
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    StudentRestriction,
    WeatherWarning,
)

from .factories import (
    AISuggestedWarningFactory,
    BlockingRestrictionFactory,
    EmergencyContactFactory,
    EquipmentFactory,
    EquipmentSafetyCheckFactory,
    EvacuationPlanFactory,
    LifeguardAssignmentFactory,
    LifeguardUserFactory,
    SafetyIncidentFactory,
    SafetyUserFactory,
    SeriousIncidentFactory,
    StudentRestrictionFactory,
    SurfSpotFactory,
    WeatherWarningFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# SafetyIncident
# ---------------------------------------------------------------------------
def test_incident_code_is_sequential_and_unique():
    first = SafetyIncidentFactory()
    second = SafetyIncidentFactory()
    assert first.incident_code == "INC00001"
    assert second.incident_code == "INC00002"
    assert str(first).startswith("INC00001")


def test_incident_is_open_and_days_open():
    incident = SafetyIncidentFactory(
        occurred_at=timezone.now() - timedelta(days=3), status=GenericStatus.OPEN
    )
    assert incident.is_open is True
    assert incident.days_open == 3

    incident.status = GenericStatus.CLOSED
    assert incident.is_open is False


def test_incident_cannot_be_recorded_in_the_future():
    incident = SafetyIncidentFactory.build(occurred_at=timezone.now() + timedelta(days=1))
    with pytest.raises(ValidationError) as error:
        incident.clean()
    assert "occurred_at" in error.value.message_dict


def test_follow_up_needs_a_due_date():
    incident = SafetyIncidentFactory.build(follow_up_required=True, follow_up_due=None)
    with pytest.raises(ValidationError) as error:
        incident.clean()
    assert "follow_up_due" in error.value.message_dict


def test_serious_incident_cannot_be_closed_without_a_corrective_action():
    incident = SeriousIncidentFactory()
    incident.status = GenericStatus.CLOSED
    with pytest.raises(ValidationError) as error:
        incident.clean()
    assert "corrective_action" in error.value.message_dict


def test_emergency_services_call_requires_the_action_taken():
    incident = SafetyIncidentFactory.build(
        emergency_services_called=True, immediate_action=""
    )
    with pytest.raises(ValidationError) as error:
        incident.clean()
    assert "immediate_action" in error.value.message_dict


def test_follow_up_overdue_only_while_open():
    incident = SafetyIncidentFactory(
        follow_up_required=True,
        follow_up_due=timezone.localdate() - timedelta(days=2),
    )
    assert incident.is_follow_up_overdue is True

    incident.status = GenericStatus.CLOSED
    assert incident.is_follow_up_overdue is False


# ---------------------------------------------------------------------------
# LifeguardAssignment
# ---------------------------------------------------------------------------
def test_shift_must_end_after_it_starts():
    assignment = LifeguardAssignmentFactory.build(
        spot=SurfSpotFactory(), lifeguard=LifeguardUserFactory(),
        start_time=time(14, 0), end_time=time(9, 0),
    )
    with pytest.raises(ValidationError) as error:
        assignment.clean()
    assert "end_time" in error.value.message_dict


def test_one_lifeguard_cannot_cover_two_overlapping_shifts():
    existing = LifeguardAssignmentFactory(start_time=time(9, 0), end_time=time(13, 0))
    clash = LifeguardAssignment(
        spot=SurfSpotFactory(),
        lifeguard=existing.lifeguard,
        date=existing.date,
        start_time=time(12, 0),
        end_time=time(16, 0),
    )
    with pytest.raises(ValidationError) as error:
        clash.clean()
    assert "start_time" in error.value.message_dict


def test_shift_duration_and_coverage():
    assignment = LifeguardAssignmentFactory(start_time=time(9, 0), end_time=time(17, 0))
    assert assignment.duration_minutes == 480

    midday = timezone.make_aware(
        datetime.combine(assignment.date, time(12, 0)), timezone.get_current_timezone()
    )
    assert assignment.covers(midday) is True

    before = timezone.make_aware(
        datetime.combine(assignment.date, time(7, 0)), timezone.get_current_timezone()
    )
    assert assignment.covers(before) is False


# ---------------------------------------------------------------------------
# EmergencyContact
# ---------------------------------------------------------------------------
def test_contact_without_a_spot_applies_everywhere():
    contact = EmergencyContactFactory(spot=None)
    assert contact.applies_everywhere is True

    spot = SurfSpotFactory()
    local = EmergencyContactFactory(spot=spot)
    assert local.applies_everywhere is False
    assert local.scope_label == spot.name


def test_alternate_number_may_not_repeat_the_main_one():
    contact = EmergencyContactFactory.build(phone="112", alternate_phone="112")
    with pytest.raises(ValidationError) as error:
        contact.clean()
    assert "alternate_phone" in error.value.message_dict


# ---------------------------------------------------------------------------
# EvacuationPlan
# ---------------------------------------------------------------------------
def test_plan_needs_at_least_one_step():
    plan = EvacuationPlanFactory.build(spot=SurfSpotFactory(), steps=[])
    with pytest.raises(ValidationError) as error:
        plan.clean()
    assert "steps" in error.value.message_dict


def test_plan_strips_blank_steps_and_counts_them():
    plan = EvacuationPlan(
        spot=SurfSpotFactory(),
        title="Lightning",
        trigger_conditions="Thunder heard.",
        assembly_point="Car park",
        steps=["Clear the water", "   ", "Count heads"],
    )
    plan.clean()
    assert plan.steps == ["Clear the water", "Count heads"]
    assert plan.step_count == 2


def test_drill_overdue_flag():
    plan = EvacuationPlanFactory(
        last_drill_date=timezone.localdate() - timedelta(days=400),
        next_drill_due=timezone.localdate() - timedelta(days=35),
    )
    assert plan.is_drill_overdue is True
    assert plan.days_until_drill == -35


# ---------------------------------------------------------------------------
# EquipmentSafetyCheck
# ---------------------------------------------------------------------------
def test_failed_check_must_say_what_is_wrong():
    check = EquipmentSafetyCheckFactory.build(
        equipment=EquipmentFactory(), passed=False, issues_found=""
    )
    with pytest.raises(ValidationError) as error:
        check.clean()
    assert "issues_found" in error.value.message_dict


def test_a_failed_checklist_item_cannot_be_a_pass():
    check = EquipmentSafetyCheck(
        equipment=EquipmentFactory(),
        passed=True,
        checklist={"Leash and leash plug": False},
        issues_found="Leash plug pulling out.",
    )
    with pytest.raises(ValidationError) as error:
        check.clean()
    assert "passed" in error.value.message_dict


def test_failed_items_are_listed():
    check = EquipmentSafetyCheckFactory(
        passed=False,
        checklist={"Leash": False, "Fins": True, "Deck": False},
        issues_found="Leash frayed, deck delaminating.",
    )
    assert check.failed_items == ["Deck", "Leash"]
    assert check.passed_items == ["Fins"]


# ---------------------------------------------------------------------------
# WeatherWarning — the AI/human separation
# ---------------------------------------------------------------------------
def test_manual_warning_is_authoritative_immediately():
    warning = WeatherWarningFactory()
    assert warning.is_authoritative is True
    assert warning.awaiting_confirmation is False
    assert warning.display_title == warning.title


def test_ai_suggestion_is_not_authoritative_until_confirmed():
    warning = AISuggestedWarningFactory()
    assert warning.ai_suggested is True
    assert warning.is_authoritative is False
    assert warning.awaiting_confirmation is True
    assert warning.is_in_force is False
    assert "AI Recommendation" in warning.display_title
    assert "awaiting staff confirmation" in warning.display_title

    warning.acknowledged_by = SafetyUserFactory()
    warning.acknowledged_at = timezone.now()
    warning.save()

    assert warning.is_authoritative is True
    assert warning.awaiting_confirmation is False
    assert warning.display_title == warning.title


def test_an_inactive_ai_suggestion_is_never_authoritative():
    warning = AISuggestedWarningFactory(
        acknowledged_by=SafetyUserFactory(), acknowledged_at=timezone.now(), is_active=False
    )
    assert warning.is_authoritative is False


def test_source_and_flag_stay_consistent():
    warning = WeatherWarningFactory.build(
        source=WeatherWarning.Source.MANUAL,
        ai_suggested=True,
        ai_rationale="Model output.",
    )
    warning.clean()
    assert warning.source == WeatherWarning.Source.AI_SUGGESTED

    other = WeatherWarningFactory.build(
        source=WeatherWarning.Source.AI_SUGGESTED, ai_suggested=False, ai_rationale="Because."
    )
    other.clean()
    assert other.ai_suggested is True


def test_ai_suggestion_must_carry_its_reasoning():
    warning = AISuggestedWarningFactory.build(ai_rationale="")
    with pytest.raises(ValidationError) as error:
        warning.clean()
    assert "ai_rationale" in error.value.message_dict


def test_warning_window_must_be_ordered():
    warning = WeatherWarningFactory.build(
        starts_at=timezone.now(), ends_at=timezone.now() - timedelta(hours=1)
    )
    with pytest.raises(ValidationError) as error:
        warning.clean()
    assert "ends_at" in error.value.message_dict


def test_high_severity_warning_in_force_blocks():
    warning = WeatherWarningFactory(severity=Severity.HIGH)
    assert warning.is_blocking is True
    assert WeatherWarningFactory(severity=Severity.LOW).is_blocking is False


# ---------------------------------------------------------------------------
# StudentRestriction
# ---------------------------------------------------------------------------
def test_restriction_is_current_inside_its_window():
    restriction = StudentRestrictionFactory(
        starts_on=timezone.localdate() - timedelta(days=1),
        ends_on=timezone.localdate() + timedelta(days=5),
    )
    assert restriction.is_current is True

    future = StudentRestrictionFactory(starts_on=timezone.localdate() + timedelta(days=2))
    assert future.is_current is False

    expired = StudentRestrictionFactory(
        starts_on=timezone.localdate() - timedelta(days=10),
        ends_on=timezone.localdate() - timedelta(days=1),
    )
    assert expired.is_current is False


def test_cannot_surf_rejects_threshold_limits():
    restriction = BlockingRestrictionFactory.build(max_wave_height_m=1.0)
    with pytest.raises(ValidationError) as error:
        restriction.clean()
    assert "cannot_surf" in error.value.message_dict


def test_temporary_restriction_needs_an_end_date():
    restriction = StudentRestrictionFactory.build(
        restriction_type=StudentRestriction.RestrictionType.TEMPORARY, ends_on=None
    )
    with pytest.raises(ValidationError) as error:
        restriction.clean()
    assert "ends_on" in error.value.message_dict


def test_a_restriction_that_limits_nothing_is_rejected():
    restriction = StudentRestrictionFactory.build(
        max_wave_height_m=None,
        max_wind_kmh=None,
        requires_supervision=False,
        cannot_surf=False,
    )
    with pytest.raises(ValidationError) as error:
        restriction.clean()
    assert "description" in error.value.message_dict


def test_limit_summary_reads_in_plain_language():
    restriction = StudentRestrictionFactory(max_wave_height_m=1.2, max_wind_kmh=30)
    summary = restriction.limit_summary
    assert any("1.2" in line for line in summary)
    assert any("30" in line for line in summary)
