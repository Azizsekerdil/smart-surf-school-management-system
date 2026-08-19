"""Read queries for surf camps.

Annotating occupancy in SQL keeps the camp list a single query instead of one
per card — the list is the busiest screen in the module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from .models import (
    ACTIVE_PARTICIPANT_STATUSES,
    CLOSED_CAMP_STATUSES,
    CampParticipant,
    CampStatus,
    RoomType,
    SurfCamp,
)

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)

#: Only live places count towards occupancy — a cancelled place is free again.
_ACTIVE_PLACES = Q(participants__status__in=ACTIVE_PARTICIPANT_STATUSES) & Q(
    participants__is_deleted=False
)


def camps_with_occupancy() -> QuerySet[SurfCamp]:
    """Camps annotated with ``booked_count``, ``single_room_count`` and ``places_left``."""
    return (
        SurfCamp.objects.select_related("spot", "lead_instructor")
        .annotate(
            booked_count=Count("participants", filter=_ACTIVE_PLACES, distinct=True),
            single_room_count=Count(
                "participants",
                filter=_ACTIVE_PLACES & Q(participants__room_type=RoomType.SINGLE),
                distinct=True,
            ),
        )
        .annotate(places_left=Greatest(F("capacity") - F("booked_count"), Value(0)))
    )


def upcoming_camps(limit: int | None = None) -> QuerySet[SurfCamp]:
    """Live camps that have not finished yet, soonest first."""
    queryset = (
        camps_with_occupancy()
        .filter(is_active=True, end_date__gte=timezone.localdate())
        .exclude(status__in=CLOSED_CAMP_STATUSES)
        .order_by("start_date")
    )
    return queryset[:limit] if limit else queryset


def running_camps(on_date: date | None = None) -> QuerySet[SurfCamp]:
    """Camps in progress on *on_date* (today by default)."""
    day = on_date or timezone.localdate()
    return (
        camps_with_occupancy()
        .filter(is_active=True, start_date__lte=day, end_date__gte=day)
        .exclude(status=CampStatus.CANCELLED)
        .order_by("start_date")
    )


def participants_for(camp: SurfCamp, include_cancelled: bool = False) -> QuerySet[CampParticipant]:
    """Participants of a camp, ordered the way the room list is read."""
    queryset = CampParticipant.objects.filter(camp=camp).select_related(
        "student", "booking", "camp"
    )
    if not include_cancelled:
        queryset = queryset.filter(status__in=ACTIVE_PARTICIPANT_STATUSES)
    return queryset.order_by("room_number", "student_id")


def collected_total(camp: SurfCamp) -> Decimal:
    """Money recorded against the live places of a camp."""
    return CampParticipant.objects.filter(
        camp=camp, status__in=ACTIVE_PARTICIPANT_STATUSES
    ).aggregate(total=Coalesce(Sum("amount_paid"), Value(ZERO), output_field=MONEY))["total"]


def students_on_camp_ids(camp: SurfCamp) -> list[int]:
    """Primary keys of students already holding a live place — used to keep the
    "add participant" picker free of duplicates."""
    return list(
        CampParticipant.objects.filter(
            camp=camp, status__in=ACTIVE_PARTICIPANT_STATUSES
        ).values_list("student_id", flat=True)
    )
