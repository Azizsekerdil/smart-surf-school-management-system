"""Read queries for surf locations.

Kept apart from :mod:`apps.locations.services` because these only shape data —
they never decide anything. Every list screen and API call goes through here so
the prefetching is written once and an N+1 cannot creep back in.
"""

from __future__ import annotations

from django.db.models import (
    Case,
    Count,
    IntegerField,
    Prefetch,
    Q,
    QuerySet,
    Value,
    When,
)

from apps.core.enums import Severity

from .models import SEVERITY_RANK, SpotHazard, SurfSpot


def _severity_ordering():
    """Order hazards by real seriousness, not by the alphabet."""
    return Case(
        *[When(severity=value, then=Value(rank)) for value, rank in SEVERITY_RANK.items()],
        default=Value(0),
        output_field=IntegerField(),
    )


def hazards_for_spot(spot: SurfSpot, *, only_active: bool = True) -> QuerySet[SpotHazard]:
    """Hazards of one spot, most serious first."""
    queryset = spot.hazards.all()
    if only_active:
        queryset = queryset.filter(is_active=True)
    return queryset.annotate(severity_order=_severity_ordering()).order_by(
        "-severity_order", "name"
    )


def active_hazard_prefetch() -> Prefetch:
    """Prefetch of active hazards, severity-ordered, onto ``prefetched_active_hazards``."""
    return Prefetch(
        "hazards",
        queryset=SpotHazard.objects.filter(is_active=True)
        .annotate(severity_order=_severity_ordering())
        .order_by("-severity_order", "name"),
        to_attr="prefetched_active_hazards",
    )


def spot_queryset(*, include_inactive: bool = True) -> QuerySet[SurfSpot]:
    """The standard surf-spot queryset: hazards prefetched, counters annotated."""
    queryset = SurfSpot.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return (
        queryset.select_related("created_by", "updated_by")
        .prefetch_related(active_hazard_prefetch())
        .annotate(
            active_hazard_count=Count(
                "hazards", filter=Q(hazards__is_active=True), distinct=True
            ),
            serious_hazard_count=Count(
                "hazards",
                filter=Q(
                    hazards__is_active=True,
                    hazards__severity__in=[Severity.HIGH, Severity.CRITICAL],
                ),
                distinct=True,
            ),
        )
    )


def spot_detail_queryset() -> QuerySet[SurfSpot]:
    """Detail-page queryset — every hazard, not only the open ones."""
    return (
        SurfSpot.objects.select_related("created_by", "updated_by")
        .prefetch_related(active_hazard_prefetch())
        .annotate(
            active_hazard_count=Count(
                "hazards", filter=Q(hazards__is_active=True), distinct=True
            )
        )
    )


def spots_with_lifeguard() -> QuerySet[SurfSpot]:
    return SurfSpot.objects.filter(is_active=True, lifeguard_on_duty=True).order_by("name")


def spot_choices() -> list[tuple[int, str]]:
    """``(pk, label)`` pairs for the spot pickers other modules render."""
    return [
        (spot.pk, f"{spot.code} · {spot.name}")
        for spot in SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")
    ]
