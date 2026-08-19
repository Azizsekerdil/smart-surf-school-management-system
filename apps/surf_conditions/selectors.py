"""Read queries for surf conditions.

Separate from :mod:`apps.surf_conditions.services` because nothing in here
decides anything — it only shapes data. Every list screen and API endpoint goes
through these helpers so the prefetching is written once and an N+1 cannot creep
back in: scoring a reading touches ``condition.spot.beach_facing_deg``, which is
exactly the kind of access that turns a 24-row table into 24 queries.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, time, timedelta

from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from .models import ConditionForecast, SurfCondition, SurfScore


def score_prefetch() -> Prefetch:
    """Scores attached in level order, so the gauges render without extra queries."""
    return Prefetch("scores", queryset=SurfScore.objects.order_by("level"))


def condition_queryset(*, with_scores: bool = True) -> QuerySet[SurfCondition]:
    """The standard condition queryset: spot joined, scores prefetched."""
    queryset = SurfCondition.objects.select_related("spot", "created_by")
    if with_scores:
        queryset = queryset.prefetch_related(score_prefetch())
    return queryset


def observations_for_spot(spot, *, limit: int | None = None) -> QuerySet[SurfCondition]:
    """Past observations at *spot*, newest first."""
    queryset = (
        condition_queryset()
        .filter(spot=spot, is_forecast=False)
        .order_by("-recorded_at")
    )
    return queryset[:limit] if limit else queryset


def forecast_hours(spot, *, start=None, end=None) -> QuerySet[SurfCondition]:
    """Stored forecast hours for *spot* inside an optional window."""
    queryset = (
        condition_queryset(with_scores=False)
        .filter(spot=spot, is_forecast=True)
        .order_by("recorded_at")
    )
    if start is not None:
        queryset = queryset.filter(recorded_at__gte=start)
    if end is not None:
        queryset = queryset.filter(recorded_at__lte=end)
    return queryset


def conditions_between(spot, start: datetime, end: datetime) -> QuerySet[SurfCondition]:
    return (
        condition_queryset(with_scores=False)
        .filter(spot=spot, recorded_at__gte=start, recorded_at__lte=end)
        .order_by("recorded_at")
    )


def day_bounds(day: date_cls) -> tuple[datetime, datetime]:
    """Aware start and end instants of a local calendar day."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def daily_forecasts(spot, *, days: int = 7) -> QuerySet[ConditionForecast]:
    today = timezone.localdate()
    return ConditionForecast.objects.filter(
        spot=spot, date__gte=today, date__lte=today + timedelta(days=max(1, days) - 1)
    ).order_by("date")


def spots_with_conditions() -> QuerySet:
    """Active spots, default first — the spot picker on every conditions screen."""
    from apps.locations.models import SurfSpot

    return SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")


def latest_observation_map(spots) -> dict[int, SurfCondition]:
    """``{spot_id: newest observation}`` for a collection of spots, in two queries.

    Used by the multi-spot overview so a school with eight breaks does not issue
    eight queries to draw one table.
    """
    spot_ids = [spot.pk for spot in spots]
    if not spot_ids:
        return {}

    rows = (
        SurfCondition.objects.filter(spot_id__in=spot_ids, is_forecast=False)
        .select_related("spot")
        .prefetch_related(score_prefetch())
        .order_by("spot_id", "-recorded_at")
    )
    newest: dict[int, SurfCondition] = {}
    for row in rows:
        newest.setdefault(row.spot_id, row)
    return newest
