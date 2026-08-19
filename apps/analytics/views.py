"""The analytics dashboard, its CSV export and its two lazy panels.

The page is assembled in one pass by :func:`apps.analytics.services.dashboard_metrics`
so the tiles, the charts and the export can never disagree with each other.

Two things load afterwards over HTMX rather than blocking the first paint:

* the statistical summary, so changing the analysed series does not reload the
  whole page;
* the AI narrative, which involves a network call to a model provider and must
  never hold up a screen full of real numbers.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.csv_safety import safe_csv_writer
from apps.core.mixins import DateRangeMixin
from apps.core.utils import parse_date_range, previous_period

from . import services, statistics
from .forms import (
    DEFAULT_HORIZON,
    FORECAST_HORIZONS,
    AnalyticsFilterForm,
    MetricChoiceForm,
)

logger = logging.getLogger("apps.analytics")

#: Window of the moving-average overlay on the revenue chart.
REVENUE_MOVING_AVERAGE_WINDOW = 7


def _selected_metric(request) -> str:
    """The series the statistical summary describes, validated against the menu."""
    requested = (request.GET.get("metric") or "revenue").strip()
    allowed = {key for key, _label in services.ANALYSABLE_METRICS}
    return requested if requested in allowed else "revenue"


def _selected_horizon(request) -> int:
    """Forecast horizon in days, validated against the offered choices."""
    requested = (request.GET.get("horizon") or DEFAULT_HORIZON).strip()
    allowed = {value for value, _label in FORECAST_HORIZONS}
    if requested not in allowed:
        requested = DEFAULT_HORIZON
    return int(requested)


class AnalyticsDashboardView(CapabilityRequiredMixin, DateRangeMixin, TemplateView):
    """Headline numbers, charts and a forecast for the chosen period."""

    capability = "analytics.view"
    template_name = "analytics/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, range_label = parse_date_range(self.request)
        start, end = services.normalise_range(start, end)
        previous_start, previous_end = previous_period(start, end)

        metrics = services.dashboard_metrics(start, end)
        horizon = _selected_horizon(self.request)
        forecast = services.revenue_forecast(days=horizon)
        metric_key = _selected_metric(self.request)

        context["filter_form"] = AnalyticsFilterForm(
            initial={
                "range": self.request.GET.get("range", "30"),
                "start": self.request.GET.get("start", ""),
                "end": self.request.GET.get("end", ""),
                "horizon": str(horizon),
            }
        )
        context["metric_form"] = MetricChoiceForm(initial={"metric": metric_key})
        context["range_label"] = range_label
        context["range_start"] = start
        context["range_end"] = end
        context["previous_start"] = previous_start
        context["previous_end"] = previous_end
        context["metrics"] = metrics
        context["forecast"] = forecast
        context["selected_metric"] = metric_key
        context["horizon"] = horizon
        context["summary"] = services.statistical_summary(metrics, metric_key)
        context["chart_data"] = self.build_chart_data(metrics, forecast)
        context["export_query"] = self.request.GET.urlencode()
        context["range_key"] = self.request.GET.get("range", "30")
        context["range_start_raw"] = self.request.GET.get("start", "")
        context["range_end_raw"] = self.request.GET.get("end", "")
        context["ai_enabled"] = bool(
            self.request.user.is_authenticated
            and self.request.user.has_capability("ai.view")
        )
        return context

    def build_chart_data(self, metrics: dict, forecast: dict) -> dict:
        """Everything Chart.js needs, as one JSON blob.

        Built server-side so the browser never recomputes a business figure —
        the chart and the tile above it are the same number by construction.
        """
        revenue = metrics["revenue"]
        revenue_values = services.series_values(revenue["series"])
        smoothed = statistics.moving_average(revenue_values, REVENUE_MOVING_AVERAGE_WINDOW)

        occupancy_types = metrics["occupancy"].get("by_type", [])[:10]
        equipment_items = metrics["equipment"].get("by_item", [])[:10]

        history = forecast.get("history", [])
        # Show at most the last 60 days of history next to the projection, so
        # the forecast is not a footnote at the right edge of a year-long line.
        history_tail = history[-60:]

        return {
            "revenue": {
                "labels": [point["label"] for point in revenue["series"]],
                "values": revenue_values,
                "moving_average": smoothed,
                "moving_average_window": REVENUE_MOVING_AVERAGE_WINDOW,
            },
            "bookings_by_status": {
                "labels": [str(row["label"]) for row in metrics["bookings"].get("status_breakdown", [])],
                "values": [row["count"] for row in metrics["bookings"].get("status_breakdown", [])],
            },
            "occupancy_by_type": {
                "labels": [str(row["label"]) for row in occupancy_types],
                "values": [row["rate"] for row in occupancy_types],
            },
            "customers": {
                "labels": [str(row["label"]) for row in metrics["customers"].get("new_vs_returning", [])],
                "values": [row["count"] for row in metrics["customers"].get("new_vs_returning", [])],
            },
            "equipment": {
                "labels": [row["label"] for row in equipment_items],
                "values": [row["hours"] for row in equipment_items],
            },
            "hours": {
                "labels": [row["label"] for row in metrics["hours"].get("hours", [])],
                "values": [row["seats"] for row in metrics["hours"].get("hours", [])],
                "lessons": [row["lessons"] for row in metrics["hours"].get("hours", [])],
            },
            "weekdays": {
                "labels": [str(row["label"]) for row in metrics["weekdays"].get("weekdays", [])],
                "values": [row["seats"] for row in metrics["weekdays"].get("weekdays", [])],
            },
            "channels": {
                "labels": [str(row["label"]) for row in metrics["channels"].get("channels", [])],
                "values": [row["count"] for row in metrics["channels"].get("channels", [])],
            },
            "levels": {
                "labels": [str(row["label"]) for row in metrics["levels"].get("distribution", [])],
                "values": [row["count"] for row in metrics["levels"].get("distribution", [])],
            },
            "forecast": {
                "history_labels": [point["label"] for point in history_tail],
                "history_values": [point["value"] for point in history_tail],
                "labels": [point["label"] for point in forecast.get("series", [])],
                "values": [point["value"] for point in forecast.get("series", [])],
                "low_confidence": bool(forecast.get("low_confidence", True)),
            },
            "labels": {
                "revenue": str(_("Revenue")),
                "moving_average": str(
                    _("%(days)s-period average") % {"days": REVENUE_MOVING_AVERAGE_WINDOW}
                ),
                "bookings": str(_("Bookings")),
                "occupancy": str(_("Occupancy %")),
                "customers": str(_("Customers")),
                "hours": str(_("Hours hired")),
                "seats": str(_("Seats")),
                "history": str(_("Actual")),
                "forecast": str(_("Projected")),
            },
        }


class StatisticalSummaryView(CapabilityRequiredMixin, View):
    """HTMX endpoint: the statistical summary panel for one chosen series."""

    capability = "analytics.view"

    def get(self, request, *args, **kwargs):
        start, end, _label = parse_date_range(request)
        start, end = services.normalise_range(start, end)
        metrics = services.dashboard_metrics(start, end)
        metric_key = _selected_metric(request)
        return render(
            request,
            "analytics/partials/statistical_summary.html",
            {
                "summary": services.statistical_summary(metrics, metric_key),
                "selected_metric": metric_key,
                "metric_form": MetricChoiceForm(initial={"metric": metric_key}),
                "range_key": request.GET.get("range", "30"),
                "range_start_raw": request.GET.get("start", ""),
                "range_end_raw": request.GET.get("end", ""),
            },
        )


class AINarrativeView(CapabilityRequiredMixin, View):
    """HTMX endpoint: a plain-language reading of the already-computed numbers.

    The model receives finished figures and is asked to describe them. It cannot
    produce a number of its own, and the output is rendered inside ``.ai-surface``
    with the mandatory chip so nobody mistakes it for a system-of-record fact.

    Every failure path — no provider configured, provider down, module absent,
    empty answer — renders nothing at all. A dashboard is not the place to
    explain an AI outage.
    """

    capability = "analytics.view"

    def get(self, request, *args, **kwargs):
        if not request.user.has_capability("ai.view"):
            return HttpResponse("")

        start, end, range_label = parse_date_range(request)
        start, end = services.normalise_range(start, end)
        metrics = services.dashboard_metrics(start, end)
        forecast = services.revenue_forecast(days=_selected_horizon(request))
        payload = services.ai_narrative_payload(metrics, forecast, str(range_label))

        text, is_ai = "", False
        try:
            from apps.ai.services import summarise_for_dashboard

            text, is_ai = summarise_for_dashboard(
                request.user,
                _(
                    "Summarise how the surf school performed in this period. "
                    "Point out the one number that most deserves attention."
                ),
                payload,
            )
        except Exception as exc:  # noqa: BLE001 - the narrative is never load-bearing
            logger.info("analytics: AI narrative unavailable (%s)", exc)
            return HttpResponse("")

        if not (is_ai and text and text.strip()):
            return HttpResponse("")

        return render(
            request,
            "analytics/partials/ai_narrative.html",
            {"narrative": text.strip(), "range_label": range_label},
        )


class AnalyticsExportView(CapabilityRequiredMixin, View):
    """Download exactly what is on screen, for the same period, as CSV."""

    capability = "analytics.export"

    def get(self, request, *args, **kwargs):
        start, end, range_label = parse_date_range(request)
        start, end = services.normalise_range(start, end)
        metrics = services.dashboard_metrics(start, end)
        forecast = services.revenue_forecast(days=_selected_horizon(request))
        rows = services.export_rows(metrics, forecast, str(range_label))

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="analytics-{timezone.localdate():%Y%m%d}.csv"'
        )
        # BOM so Excel on Windows opens the Turkish characters correctly.
        response.write("﻿")
        writer = safe_csv_writer(response, lineterminator="\n")
        for row in rows:
            writer.writerow(row)
        return response
