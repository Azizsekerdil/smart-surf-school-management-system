"""Dashboard controls.

There is no model form here: analytics writes nothing from the UI. These forms
exist to give the filter bar the same styling, labels and validation as every
other screen in the product.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import TailwindFormMixin

from .services import ANALYSABLE_METRICS

#: The analytics dashboard deliberately omits "all time": every figure on the
#: screen is compared against the equally long preceding period, and "all time"
#: has no predecessor to compare with.
ANALYTICS_RANGE_CHOICES: tuple[tuple[str, object], ...] = (
    ("today", _("Today")),
    ("7", _("Last 7 days")),
    ("30", _("Last 30 days")),
    ("90", _("Last 3 months")),
    ("180", _("Last 6 months")),
    ("365", _("Last year")),
    ("custom", _("Custom range")),
)

#: Forecast horizons offered in the UI. Longer than a quarter, a straight-line
#: projection from surf-school data is fiction, so it is not on the menu.
FORECAST_HORIZONS: tuple[tuple[str, object], ...] = (
    ("7", _("Next 7 days")),
    ("14", _("Next 14 days")),
    ("30", _("Next 30 days")),
    ("90", _("Next 3 months")),
)

DEFAULT_HORIZON = "30"


class AnalyticsFilterForm(TailwindFormMixin, forms.Form):
    """Period, analysed metric and forecast horizon."""

    range = forms.ChoiceField(
        label=_("Period"),
        choices=ANALYTICS_RANGE_CHOICES,
        required=False,
        initial="30",
    )
    start = forms.DateField(
        label=_("From"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        label=_("To"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    horizon = forms.ChoiceField(
        label=_("Forecast horizon"),
        choices=FORECAST_HORIZONS,
        required=False,
        initial=DEFAULT_HORIZON,
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            raise forms.ValidationError(_("The end date must not be before the start date."))
        return cleaned


class MetricChoiceForm(TailwindFormMixin, forms.Form):
    """Which series the statistical summary panel describes.

    Kept separate from the filter bar because it swaps one panel over HTMX
    rather than reloading the whole dashboard.
    """

    metric = forms.ChoiceField(
        label=_("Analysed series"),
        choices=ANALYSABLE_METRICS,
        required=False,
        initial="revenue",
    )
