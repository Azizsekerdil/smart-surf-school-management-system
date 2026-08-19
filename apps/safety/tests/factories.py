"""Factories for safety test data.

``AISuggestedWarningFactory`` is deliberately separate from
``WeatherWarningFactory``: half the tests in this module exist to prove that an
unconfirmed AI suggestion is invisible to everything except the sign-off screen.
"""

from __future__ import annotations

from datetime import time, timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.accounts.constants import Role
from apps.core.enums import GenericStatus, Severity
from apps.equipment.tests.factories import EquipmentFactory
from apps.locations.tests.factories import SurfSpotFactory
from apps.safety.models import (
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)
from apps.students.tests.factories import StudentFactory

__all__ = [
    "SafetyUserFactory",
    "LifeguardUserFactory",
    "SafetyIncidentFactory",
    "LifeguardAssignmentFactory",
    "EmergencyContactFactory",
    "EvacuationPlanFactory",
    "EquipmentSafetyCheckFactory",
    "WeatherWarningFactory",
    "AISuggestedWarningFactory",
    "StudentRestrictionFactory",
    "SurfSpotFactory",
    "StudentFactory",
    "EquipmentFactory",
]


class SafetyUserFactory(DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"safety-staff{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.test")
    first_name = "Safety"
    last_name = "Officer"
    role = Role.MANAGER
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or "surf-test-password-1")
        self.save(update_fields=["password"])


class LifeguardUserFactory(SafetyUserFactory):
    username = factory.Sequence(lambda n: f"lifeguard{n}")
    first_name = "Water"
    last_name = "Cover"
    role = Role.LIFEGUARD


class SafetyIncidentFactory(DjangoModelFactory):
    class Meta:
        model = SafetyIncident

    spot = factory.SubFactory(SurfSpotFactory)
    occurred_at = factory.LazyFunction(lambda: timezone.now() - timedelta(hours=2))
    incident_type = SafetyIncident.IncidentType.NEAR_MISS
    severity = Severity.LOW
    status = GenericStatus.OPEN
    description = "A student drifted outside the teaching zone and was walked back in."
    immediate_action = "Whistle recall, group repositioned inside the flags."
    conditions_at_time = factory.LazyFunction(
        lambda: {"wave_height_m": 0.8, "wind_speed_kmh": 14.0, "tide": "mid_rising"}
    )


class SeriousIncidentFactory(SafetyIncidentFactory):
    incident_type = SafetyIncident.IncidentType.INJURY
    severity = Severity.HIGH
    medical_attention_required = True
    description = "Fin laceration to the lower leg during a wipe-out."
    immediate_action = "Pressure dressing applied, casualty walked to the first-aid point."


class LifeguardAssignmentFactory(DjangoModelFactory):
    class Meta:
        model = LifeguardAssignment

    spot = factory.SubFactory(SurfSpotFactory)
    lifeguard = factory.SubFactory(LifeguardUserFactory)
    date = factory.LazyFunction(timezone.localdate)
    start_time = time(9, 0)
    end_time = time(17, 0)
    is_confirmed = True


class EmergencyContactFactory(DjangoModelFactory):
    class Meta:
        model = EmergencyContact

    name = factory.Sequence(lambda n: f"Emergency contact {n}")
    organisation = "Coastal Services"
    kind = EmergencyContact.Kind.AMBULANCE
    phone = "112"
    sort_order = 10
    is_active = True


class EvacuationPlanFactory(DjangoModelFactory):
    class Meta:
        model = EvacuationPlan

    spot = factory.SubFactory(SurfSpotFactory)
    title = factory.Sequence(lambda n: f"Beach clearance plan {n}")
    trigger_conditions = "Red flag raised, lightning within 10 km, or a missing person."
    assembly_point = "Car park, next to the lifeguard hut"
    steps = factory.LazyFunction(
        lambda: [
            "Long whistle blast, arm raised — everybody out of the water.",
            "Head count at the water's edge against the session register.",
            "Move the group to the assembly point.",
            "Second head count. Report the number to the duty manager.",
        ]
    )
    responsible_role = Role.HEAD_INSTRUCTOR
    is_active = True


class EquipmentSafetyCheckFactory(DjangoModelFactory):
    class Meta:
        model = EquipmentSafetyCheck

    equipment = factory.SubFactory(EquipmentFactory)
    checked_by = factory.SubFactory(SafetyUserFactory)
    checked_at = factory.LazyFunction(timezone.now)
    passed = True
    checklist = factory.LazyFunction(
        lambda: {"Leash and leash plug": True, "Fins and fin boxes": True}
    )


class WeatherWarningFactory(DjangoModelFactory):
    """A warning a person entered: authoritative from the moment it is saved."""

    class Meta:
        model = WeatherWarning

    spot = None
    title = factory.Sequence(lambda n: f"Strong onshore wind {n}")
    severity = Severity.MEDIUM
    source = WeatherWarning.Source.MANUAL
    description = "Gusts to 45 km/h from mid-morning."
    starts_at = factory.LazyFunction(lambda: timezone.now() - timedelta(hours=1))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=6))
    is_active = True
    ai_suggested = False


class AISuggestedWarningFactory(WeatherWarningFactory):
    """An AI proposal. Not a warning until a named person confirms it."""

    title = factory.Sequence(lambda n: f"Possible rip development {n}")
    source = WeatherWarning.Source.AI_SUGGESTED
    ai_suggested = True
    ai_rationale = (
        "Swell period rose from 8 s to 13 s over three hours while the tide is "
        "falling; the sandbar configuration at this spot has historically produced "
        "a rip in this combination."
    )


class StudentRestrictionFactory(DjangoModelFactory):
    class Meta:
        model = StudentRestriction

    student = factory.SubFactory(StudentFactory)
    restriction_type = StudentRestriction.RestrictionType.MEDICAL
    description = "Recovering shoulder injury — no paddling in heavy water."
    max_wave_height_m = 1.0
    requires_supervision = False
    cannot_surf = False
    starts_on = factory.LazyFunction(timezone.localdate)
    is_active = True


class BlockingRestrictionFactory(StudentRestrictionFactory):
    restriction_type = StudentRestriction.RestrictionType.MEDICAL
    description = "Post-concussion protocol — no water time until cleared."
    max_wave_height_m = None
    max_wind_kmh = None
    cannot_surf = True
