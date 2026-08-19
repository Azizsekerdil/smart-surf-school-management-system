"""Read queries for the instructor screens.

Kept apart from :mod:`services` because these functions only ever read: they
shape data for a template or a serializer and never change state. Anything that
touches another app is defensive, so a profile page renders correctly on a
fresh installation where lessons have never been recorded.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.core.exceptions import FieldError
from django.db import DatabaseError
from django.db.models import Count, Q
from django.utils import timezone

from .models import AvailabilitySlot, Certification, Instructor, PerformanceReview, TimeOff
from .services import _lesson_base_queryset, _lesson_schema  # noqa: PLC2701 - same package

logger = logging.getLogger(__name__)


def instructor_queryset():
    """Base queryset for every list screen: one query, no N+1."""
    return Instructor.objects.select_related("user").prefetch_related("certifications")


def annotate_certification_counts(queryset):
    """Attach expiring / expired certification counts for the list badges."""
    today = timezone.localdate()
    horizon = today + dt.timedelta(days=60)
    return queryset.annotate(
        expiring_count=Count(
            "certifications",
            filter=Q(
                certifications__is_deleted=False,
                certifications__expires_on__gte=today,
                certifications__expires_on__lte=horizon,
            ),
            distinct=True,
        ),
        expired_count=Count(
            "certifications",
            filter=Q(
                certifications__is_deleted=False,
                certifications__expires_on__lt=today,
            ),
            distinct=True,
        ),
    )


def upcoming_lessons(instructor: Instructor, limit: int = 8) -> list[dict]:
    """The instructor's next lessons as plain dictionaries.

    Returns ``[]`` when the lessons app is absent, empty or shaped differently —
    the profile page must never depend on another module being finished.
    """
    schema = _lesson_schema()
    if schema is None:
        return []
    try:
        queryset = _lesson_base_queryset(schema, instructor)
        if queryset is None:
            return []
        queryset = queryset.filter(
            **{f"{schema.date_field}__gte": timezone.localdate()}
        ).order_by(schema.date_field, *( [schema.start_field] if schema.start_field else []))
        rows: list[dict] = []
        for lesson in queryset[:limit]:
            rows.append(
                {
                    "label": str(lesson),
                    "date": getattr(lesson, schema.date_field, None),
                    "start_time": getattr(lesson, schema.start_field, None)
                    if schema.start_field
                    else None,
                    "end_time": getattr(lesson, schema.end_field, None)
                    if schema.end_field
                    else None,
                    "status": getattr(lesson, schema.status_field, "")
                    if schema.status_field
                    else "",
                    "url": _safe_absolute_url(lesson),
                }
            )
        return rows
    except (DatabaseError, FieldError, TypeError, ValueError) as exc:
        logger.debug("Upcoming lessons unavailable: %s", exc)
        return []


def _safe_absolute_url(instance) -> str:
    getter = getattr(instance, "get_absolute_url", None)
    if getter is None:
        return ""
    try:
        return getter() or ""
    except Exception:  # noqa: BLE001 - a missing URL must not break the page
        return ""


def certification_summary(instructor: Instructor) -> dict:
    """Counts used by the certification card on the profile."""
    today = timezone.localdate()
    horizon = today + dt.timedelta(days=60)
    certifications = instructor.certifications.all()
    return {
        "total": certifications.count(),
        "verified": certifications.filter(is_verified=True).count(),
        "unverified": certifications.filter(is_verified=False).count(),
        "expiring": certifications.filter(
            expires_on__gte=today, expires_on__lte=horizon
        ).count(),
        "expired": certifications.filter(expires_on__lt=today).count(),
        "missing_groups": instructor.missing_certification_groups,
    }


def instructors_needing_attention(limit: int = 10) -> list[dict]:
    """Active instructors whose paperwork blocks them from teaching."""
    today = timezone.localdate()
    horizon = today + dt.timedelta(days=60)
    queryset = (
        Instructor.objects.select_related("user")
        .filter(is_active=True)
        .filter(
            Q(certifications__is_deleted=False, certifications__expires_on__lt=today)
            | Q(
                certifications__is_deleted=False,
                certifications__expires_on__gte=today,
                certifications__expires_on__lte=horizon,
            )
        )
        .distinct()
    )
    rows = []
    for instructor in queryset[:limit]:
        rows.append(
            {
                "instructor": instructor,
                "expired": list(instructor.expired_certifications),
                "expiring": list(instructor.expiring_certifications),
            }
        )
    return rows


def pending_time_off(limit: int | None = None):
    """Absence requests still waiting for a decision."""
    queryset = (
        TimeOff.objects.select_related("instructor", "instructor__user")
        .filter(is_approved=False)
        .order_by("start_date")
    )
    return queryset[:limit] if limit else queryset


def time_off_on(on_date: dt.date):
    """Approved absence covering *on_date*."""
    return (
        TimeOff.objects.select_related("instructor", "instructor__user")
        .filter(is_approved=True, start_date__lte=on_date, end_date__gte=on_date)
        .order_by("instructor__user__first_name")
    )


def roster_for_date(on_date: dt.date):
    """Instructors with published, valid availability on *on_date*."""
    weekday = on_date.weekday()
    return (
        Instructor.objects.select_related("user")
        .filter(
            is_active=True,
            is_available_for_booking=True,
            availability_slots__is_active=True,
            availability_slots__weekday=weekday,
        )
        .filter(
            Q(availability_slots__valid_from__isnull=True)
            | Q(availability_slots__valid_from__lte=on_date)
        )
        .filter(
            Q(availability_slots__valid_until__isnull=True)
            | Q(availability_slots__valid_until__gte=on_date)
        )
        .exclude(
            time_off_periods__is_deleted=False,
            time_off_periods__is_approved=True,
            time_off_periods__start_date__lte=on_date,
            time_off_periods__end_date__gte=on_date,
        )
        .distinct()
    )


def recent_reviews(instructor: Instructor, limit: int = 5):
    return (
        PerformanceReview.objects.select_related("reviewer")
        .filter(instructor=instructor)
        .order_by("-period_end")[:limit]
    )


def certifications_for(instructor: Instructor):
    return instructor.certifications.select_related("verified_by").all()


def availability_slots_for(instructor: Instructor):
    return AvailabilitySlot.objects.filter(instructor=instructor).order_by(
        "weekday", "start_time"
    )


def certification_kind_choices() -> list[tuple[str, str]]:
    return list(Certification.Kind.choices)
