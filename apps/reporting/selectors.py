"""Read queries for the reporting screens.

Kept out of the views so the history list, the REST API and the tests all count
the same things the same way.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import GeneratedReport, ReportDefinition, ReportFormat, ReportStatus


def generated_reports() -> QuerySet[GeneratedReport]:
    return GeneratedReport.objects.select_related("definition", "generated_by")


def definitions() -> QuerySet[ReportDefinition]:
    return ReportDefinition.objects.select_related("created_by")


def history_stats(queryset: QuerySet[GeneratedReport]) -> dict:
    """Headline numbers above the export history."""
    aggregate = queryset.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=ReportStatus.COMPLETED)),
        failed=Count("id", filter=Q(status=ReportStatus.FAILED)),
        rows=Sum("row_count"),
        bytes=Sum("file_size_bytes"),
    )
    return {
        "total": aggregate["total"] or 0,
        "completed": aggregate["completed"] or 0,
        "failed": aggregate["failed"] or 0,
        "rows": aggregate["rows"] or 0,
        "bytes": aggregate["bytes"] or 0,
    }


def exports_per_day(
    queryset: QuerySet[GeneratedReport], start: date | None, end: date | None
) -> dict:
    """Chart series: one bar per day, split by format.

    Days with no exports are filled in with zeros so the x-axis stays a real
    calendar rather than a list of busy days.
    """
    today = timezone.localdate()
    first = start or (today - timedelta(days=29))
    last = end or today
    if last < first:
        first, last = last, first
    # A chart with 400 bars is unreadable; cap the window at a quarter.
    if (last - first).days > 92:
        first = last - timedelta(days=92)

    rows = (
        queryset.filter(created_at__date__gte=first, created_at__date__lte=last)
        .annotate(day=TruncDate("created_at"))
        .values("day", "format")
        .annotate(total=Count("id"))
    )

    formats = [ReportFormat.PDF, ReportFormat.EXCEL, ReportFormat.CSV]
    buckets: dict[str, dict[date, int]] = {value: {} for value in formats}
    for row in rows:
        day = row["day"]
        if isinstance(day, str):  # some backends hand back an ISO string
            day = date.fromisoformat(day)
        buckets.setdefault(row["format"], {})[day] = row["total"]

    labels: list[str] = []
    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        labels.append(cursor.strftime("%d.%m"))
        cursor += timedelta(days=1)

    return {
        "labels": labels,
        "datasets": [
            {
                "label": str(ReportFormat(value).label),
                "data": [buckets.get(value, {}).get(day, 0) for day in days],
            }
            for value in formats
        ],
    }


def most_used_reports(queryset: QuerySet[GeneratedReport], limit: int = 5) -> list[dict]:
    return list(
        queryset.values("report_key")
        .annotate(total=Count("id"))
        .order_by("-total")[:limit]
    )


def recent_for_user(user, limit: int = 5) -> QuerySet[GeneratedReport]:
    """The exports this person made, for the "pick up where you left off" panel."""
    if not (user and getattr(user, "is_authenticated", False)):
        return GeneratedReport.objects.none()
    return generated_reports().filter(
        generated_by=user, status=ReportStatus.COMPLETED
    )[:limit]
