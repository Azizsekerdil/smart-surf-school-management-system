"""Stored metric values.

Why store anything at all when :mod:`apps.analytics.services` can compute every
figure live? Two reasons a real school runs into:

* **History that no longer exists.** "Occupancy in July" is derived from lessons
  and bookings as they are *now*. Once a lesson is archived or a booking is
  amended, the live query silently reports a different July. A snapshot is what
  was true when it was taken.
* **Cost.** Year-on-year charts over five seasons should not re-aggregate five
  years of bookings on every page load.

A snapshot is therefore an append-or-refresh record keyed by
``(metric_key, period_start, period_end, granularity)``. Re-running a
computation for the same window updates the same row rather than growing a pile
of near-duplicates.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

#: Value precision. Wider and finer than money on purpose: this column also
#: holds rates (0.8734), counts (412) and hours (17.5000).
VALUE_MAX_DIGITS = 14
VALUE_DECIMAL_PLACES = 4

ZERO_VALUE = Decimal("0.0000")


class MetricSnapshot(TimeStampedModel):
    """One metric, measured over one period, at one granularity."""

    class Granularity(models.TextChoices):
        DAY = "day", _("Day")
        WEEK = "week", _("Week")
        MONTH = "month", _("Month")
        YEAR = "year", _("Year")

    metric_key = models.CharField(
        _("metric"),
        max_length=100,
        db_index=True,
        help_text=_(
            "Dotted identifier of the measure, e.g. revenue.total or "
            "bookings.cancellation_rate."
        ),
    )
    period_start = models.DateField(_("period start"), db_index=True)
    period_end = models.DateField(
        _("period end"), help_text=_("Inclusive last day of the measured window.")
    )
    granularity = models.CharField(
        _("granularity"),
        max_length=5,
        choices=Granularity.choices,
        default=Granularity.DAY,
        db_index=True,
    )
    value = models.DecimalField(
        _("value"),
        max_digits=VALUE_MAX_DIGITS,
        decimal_places=VALUE_DECIMAL_PLACES,
        default=ZERO_VALUE,
        help_text=_("Money, a rate or a duration — the metric decides the unit."),
    )
    count = models.PositiveIntegerField(
        _("record count"),
        default=0,
        help_text=_("How many source rows the value was computed from."),
    )
    dimensions = models.JSONField(
        _("dimensions"),
        default=dict,
        blank=True,
        help_text=_(
            "Optional breakdown the value is sliced by, e.g. "
            '{"lesson_type": "group-beginner"}.'
        ),
    )
    computed_at = models.DateTimeField(_("computed at"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("metric snapshot")
        verbose_name_plural = _("metric snapshots")
        ordering = ["-period_start", "metric_key"]
        indexes = [
            models.Index(fields=["metric_key", "granularity", "period_start"]),
            models.Index(fields=["metric_key", "-computed_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["metric_key", "period_start", "period_end", "granularity"],
                name="uniq_metric_snapshot_window",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="metric_snapshot_period_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_key} · {self.period_start:%Y-%m-%d} → {self.period_end:%Y-%m-%d}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValidationError(
                {"period_end": _("The period end cannot be before the period start.")}
            )

    @property
    def value_as_float(self) -> float:
        """The value as a plain float, for charting and the statistics engine."""
        try:
            return float(self.value)
        except (TypeError, ValueError):
            return 0.0

    @property
    def span_days(self) -> int:
        """Length of the measured window in whole days, inclusive of both ends."""
        if not (self.period_start and self.period_end):
            return 0
        return (self.period_end - self.period_start).days + 1

    @classmethod
    def record(
        cls,
        metric_key: str,
        period_start,
        period_end,
        value,
        *,
        granularity: str = Granularity.DAY,
        count: int = 0,
        dimensions: dict | None = None,
    ) -> MetricSnapshot:
        """Create or refresh the snapshot for one window.

        Idempotent by design: running the nightly computation twice leaves one
        row, with the later ``computed_at``.
        """
        snapshot, _created = cls.objects.update_or_create(
            metric_key=metric_key,
            period_start=period_start,
            period_end=period_end,
            granularity=granularity,
            defaults={
                "value": Decimal(str(value or 0)).quantize(Decimal("0.0001")),
                "count": max(0, int(count or 0)),
                "dimensions": dimensions or {},
                "computed_at": timezone.now(),
            },
        )
        return snapshot
