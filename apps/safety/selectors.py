"""Read queries for the safety module.

These shape data; they never decide anything (that is :mod:`apps.safety.services`).
Every list screen and API endpoint goes through here so the prefetching is
written once and an N+1 cannot creep back in.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import (
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
def incident_queryset() -> QuerySet[SafetyIncident]:
    """List queryset: everything a row on the incident table shows."""
    return (
        SafetyIncident.objects.select_related("spot", "lesson", "reported_by", "reviewed_by")
        .prefetch_related("people_involved__customer", "staff_involved")
        .order_by("-occurred_at", "-id")
    )


def incident_detail_queryset() -> QuerySet[SafetyIncident]:
    return (
        SafetyIncident.objects.select_related(
            "spot", "lesson", "lesson__instructor", "reported_by", "reviewed_by", "created_by"
        )
        .prefetch_related("people_involved__customer", "staff_involved")
    )


def open_incidents() -> QuerySet[SafetyIncident]:
    from .models import OPEN_INCIDENT_STATUSES

    return incident_queryset().filter(status__in=OPEN_INCIDENT_STATUSES)


def incidents_for_spot(spot, *, limit: int | None = None) -> QuerySet[SafetyIncident]:
    queryset = incident_queryset().filter(spot=spot)
    return queryset[:limit] if limit else queryset


# ---------------------------------------------------------------------------
# Lifeguard cover
# ---------------------------------------------------------------------------
def assignment_queryset() -> QuerySet[LifeguardAssignment]:
    return LifeguardAssignment.objects.select_related("spot", "lifeguard").order_by(
        "date", "start_time"
    )


def assignments_between(start: date, end: date, *, spot=None) -> QuerySet[LifeguardAssignment]:
    queryset = assignment_queryset().filter(date__gte=start, date__lte=end)
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    return queryset


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------
def emergency_contacts(*, spot=None, only_active: bool = True) -> QuerySet[EmergencyContact]:
    """Contacts for a spot: its own first, then the ones that apply everywhere."""
    queryset = EmergencyContact.objects.select_related("spot")
    if only_active:
        queryset = queryset.filter(is_active=True)
    if spot is not None:
        queryset = queryset.filter(Q(spot__isnull=True) | Q(spot=spot))
    return queryset.order_by("sort_order", "kind", "name")


# ---------------------------------------------------------------------------
# Evacuation plans
# ---------------------------------------------------------------------------
def plan_queryset(*, only_active: bool = False) -> QuerySet[EvacuationPlan]:
    queryset = EvacuationPlan.objects.select_related("spot", "created_by")
    if only_active:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("spot__name", "title")


# ---------------------------------------------------------------------------
# Equipment checks
# ---------------------------------------------------------------------------
def check_queryset() -> QuerySet[EquipmentSafetyCheck]:
    return EquipmentSafetyCheck.objects.select_related(
        "equipment", "equipment__category", "checked_by"
    ).order_by("-checked_at", "-id")


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
def warning_queryset() -> QuerySet[WeatherWarning]:
    return WeatherWarning.objects.select_related("spot", "acknowledged_by").order_by(
        "-starts_at", "-id"
    )


def current_warnings() -> QuerySet[WeatherWarning]:
    """Every active warning whose window is open — confirmed or not.

    The safety screens need both kinds so they can show the separation; other
    modules must use :func:`apps.safety.services.authoritative_warnings`.
    """
    now = timezone.now()
    return warning_queryset().filter(is_active=True, starts_at__lte=now, ends_at__gte=now)


# ---------------------------------------------------------------------------
# Restrictions
# ---------------------------------------------------------------------------
def restriction_queryset() -> QuerySet[StudentRestriction]:
    return StudentRestriction.objects.select_related(
        "student", "student__customer", "issued_by"
    ).order_by("-is_active", "-starts_on", "-id")


def current_restrictions() -> QuerySet[StudentRestriction]:
    today = timezone.localdate()
    return (
        restriction_queryset()
        .filter(is_active=True, starts_on__lte=today)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
    )
