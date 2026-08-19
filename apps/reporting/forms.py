"""Forms for running a report and for saving a report configuration.

The filter form is *built from the report*, not hard-coded: each entry in the
catalogue declares which filters it accepts, and this module knows how to render
each name in that vocabulary. Adding a report therefore never means editing a
template.
"""

from __future__ import annotations

import json

from django import forms
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import all_capabilities
from apps.core.enums import (
    BookingStatus,
    EquipmentStatus,
    GenericStatus,
    PaymentMethod,
    PaymentStatus,
    Severity,
    SurfLevel,
)
from apps.core.forms_base import (
    CHECKBOX_CLASS,
    INPUT_CLASS,
    SELECT_CLASS,
    TailwindFormMixin,
)
from apps.core.utils import RANGE_CHOICES

from .cron import CronError, parse_cron
from .exporters.registry import available_formats
from .models import ReportDefinition, ReportFormat, ReportStatus
from .reports import ReportSpec, all_reports, get_model, get_report

#: Filter names the run form knows how to render. A report that asks for a name
#: outside this set simply gets no widget for it, never a crash.
FILTER_VOCABULARY = (
    "period",
    "date",
    "booking_status",
    "payment_status",
    "payment_method",
    "rental_status",
    "maintenance_status",
    "equipment_status",
    "equipment_category",
    "instructor",
    "camp",
    "level",
    "severity",
    "include_inactive",
    "marketing_only",
)


def _blank(choices) -> list[tuple[str, object]]:
    return [("", _("All"))] + list(choices)


class ReportFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar on the run screen, assembled for one report."""

    format = forms.ChoiceField(
        label=_("Format"), choices=(), required=True, initial=ReportFormat.PDF
    )

    def __init__(self, *args, spec: ReportSpec, **kwargs):
        self.spec = spec
        super().__init__(*args, **kwargs)
        self.fields["format"].choices = available_formats()
        self.fields["format"].initial = spec.default_format

        for name in spec.filter_fields:
            if name in FILTER_VOCABULARY:
                self._add_filter(name)

        for name, value in (spec.default_filters or {}).items():
            if name in self.fields and not self.is_bound:
                self.fields[name].initial = value

        # Re-run the Tailwind styling for the fields added after __init__.
        self._style_fields()

    # --- construction -----------------------------------------------------
    def _add_filter(self, name: str) -> None:
        builder = getattr(self, f"_field_{name}", None)
        if builder is None:
            return
        for field_name, field in builder().items():
            self.fields[field_name] = field

    def _style_fields(self) -> None:
        """Style the dynamically added widgets.

        ``TailwindFormMixin`` styles ``self.fields`` inside ``__init__``, which
        has already run by the time the report's own filters are attached, so
        the same rules are applied once more here.
        """
        for field in self.fields.values():
            widget = field.widget
            if "class" in widget.attrs and (
                INPUT_CLASS in widget.attrs["class"]
                or SELECT_CLASS in widget.attrs["class"]
                or CHECKBOX_CLASS in widget.attrs["class"]
            ):
                continue
            existing = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = f"{existing} {CHECKBOX_CLASS}".strip()
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs["class"] = f"{existing} {SELECT_CLASS}".strip()
            else:
                widget.attrs["class"] = f"{existing} {INPUT_CLASS}".strip()

    # --- individual filters ----------------------------------------------
    def _field_period(self) -> dict:
        return {
            "range": forms.ChoiceField(
                label=_("Period"), choices=RANGE_CHOICES, required=False, initial="30"
            ),
            "start": forms.DateField(
                label=_("From"),
                required=False,
                widget=forms.DateInput(attrs={"type": "date"}),
            ),
            "end": forms.DateField(
                label=_("To"),
                required=False,
                widget=forms.DateInput(attrs={"type": "date"}),
            ),
        }

    def _field_date(self) -> dict:
        return {
            "date": forms.DateField(
                label=_("Day"),
                required=False,
                initial=timezone.localdate,
                widget=forms.DateInput(attrs={"type": "date"}),
            )
        }

    def _field_booking_status(self) -> dict:
        return {
            "booking_status": forms.ChoiceField(
                label=_("Booking status"), choices=_blank(BookingStatus.choices), required=False
            )
        }

    def _field_payment_status(self) -> dict:
        return {
            "payment_status": forms.ChoiceField(
                label=_("Payment status"), choices=_blank(PaymentStatus.choices), required=False
            )
        }

    def _field_payment_method(self) -> dict:
        return {
            "payment_method": forms.ChoiceField(
                label=_("Payment method"), choices=_blank(PaymentMethod.choices), required=False
            )
        }

    def _field_rental_status(self) -> dict:
        model = get_model("rentals.Rental")
        choices = list(model.Status.choices) if model is not None else []
        return {
            "rental_status": forms.ChoiceField(
                label=_("Rental status"), choices=_blank(choices), required=False
            )
        }

    def _field_maintenance_status(self) -> dict:
        return {
            "maintenance_status": forms.ChoiceField(
                label=_("Job status"), choices=_blank(GenericStatus.choices), required=False
            )
        }

    def _field_equipment_status(self) -> dict:
        return {
            "equipment_status": forms.ChoiceField(
                label=_("Equipment status"),
                choices=_blank(EquipmentStatus.choices),
                required=False,
            )
        }

    def _field_equipment_category(self) -> dict:
        model = get_model("equipment.EquipmentCategory")
        if model is None:
            return {}
        return {
            "equipment_category": forms.ModelChoiceField(
                label=_("Category"),
                queryset=model.objects.filter(is_active=True).order_by("sort_order", "name"),
                required=False,
                empty_label=_("All categories"),
            )
        }

    def _field_instructor(self) -> dict:
        model = get_model("instructors.Instructor")
        if model is None:
            return {}
        return {
            "instructor": forms.ModelChoiceField(
                label=_("Instructor"),
                queryset=model.objects.filter(is_active=True).select_related("user"),
                required=False,
                empty_label=_("All instructors"),
            )
        }

    def _field_camp(self) -> dict:
        model = get_model("surf_camps.SurfCamp")
        if model is None:
            return {}
        return {
            "camp": forms.ModelChoiceField(
                label=_("Camp"),
                queryset=model.objects.order_by("-start_date"),
                required=False,
                empty_label=_("All camps in the period"),
            )
        }

    def _field_level(self) -> dict:
        return {
            "level": forms.ChoiceField(
                label=_("Surf level"), choices=_blank(SurfLevel.choices), required=False
            )
        }

    def _field_severity(self) -> dict:
        return {
            "severity": forms.ChoiceField(
                label=_("Severity"), choices=_blank(Severity.choices), required=False
            )
        }

    def _field_include_inactive(self) -> dict:
        return {
            "include_inactive": forms.BooleanField(
                label=_("Include inactive records"), required=False
            )
        }

    def _field_marketing_only(self) -> dict:
        return {
            "marketing_only": forms.BooleanField(
                label=_("Only customers who consented to marketing"), required=False
            )
        }

    # --- cleaning ---------------------------------------------------------
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", _("The end date must not be before the start date."))
        if (start or end) and cleaned.get("range") != "custom":
            # Silently honouring dates the user typed under a different preset
            # would produce a document whose header lies about its own period.
            cleaned["range"] = "custom"
        return cleaned

    def filter_values(self) -> dict:
        """Cleaned data as the JSON-safe dict the builders expect."""
        values: dict = {}
        for name, value in (self.cleaned_data or {}).items():
            if name == "format" or value in (None, "", False):
                continue
            if hasattr(value, "pk"):
                values[name] = value.pk
            elif hasattr(value, "isoformat"):
                values[name] = value.isoformat()
            else:
                values[name] = value
        return values

    @property
    def chosen_format(self) -> str:
        value = (self.cleaned_data or {}).get("format") if self.is_bound else None
        return value or self.spec.default_format


class ReportDefinitionForm(TailwindFormMixin, forms.ModelForm):
    """Create or edit a saved report configuration."""

    report_key = forms.ChoiceField(label=_("Report"), choices=())
    recipients = forms.CharField(
        label=_("Recipients"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "ops@surfschool.com, finance@surfschool.com"}),
        help_text=_("Comma-separated e-mail addresses. Required for a scheduled report."),
    )
    default_filters = forms.JSONField(
        label=_("Default filters"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "font-mono"}),
        help_text=_('JSON object, for example {"range": "30", "include_inactive": false}.'),
    )

    class Meta:
        model = ReportDefinition
        fields = (
            "name",
            "code",
            "report_key",
            "description",
            "default_format",
            "default_filters",
            "required_capability",
            "is_scheduled",
            "schedule_cron",
            "recipients",
            "is_active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "schedule_cron": forms.TextInput(attrs={"placeholder": "0 7 * * 1"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["report_key"].choices = [(spec.key, spec.title) for spec in all_reports()]
        self.fields["required_capability"].required = False
        if self.instance.pk:
            self.fields["recipients"].initial = ", ".join(self.instance.recipient_list)
        if not self.initial.get("default_filters") and not self.instance.pk:
            self.fields["default_filters"].initial = {}

    def clean_code(self) -> str:
        return (self.cleaned_data.get("code") or "").strip().lower()

    def clean_report_key(self) -> str:
        key = (self.cleaned_data.get("report_key") or "").strip()
        if get_report(key) is None:
            raise forms.ValidationError(_("That report does not exist in the catalogue."))
        return key

    def clean_required_capability(self) -> str:
        capability = (self.cleaned_data.get("required_capability") or "").strip()
        if capability and capability not in all_capabilities():
            raise forms.ValidationError(
                _("“%(cap)s” is not a capability defined in this system.") % {"cap": capability}
            )
        return capability

    def clean_default_filters(self) -> dict:
        value = self.cleaned_data.get("default_filters")
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise forms.ValidationError(_("Filters must be valid JSON.")) from error
        if not isinstance(value, dict):
            raise forms.ValidationError(_("Filters must be a JSON object, not a list or a value."))
        return value

    def clean_recipients(self) -> list[str]:
        raw = self.cleaned_data.get("recipients") or ""
        addresses = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
        for address in addresses:
            try:
                validate_email(address)
            except forms.ValidationError as error:
                raise forms.ValidationError(
                    _("“%(address)s” is not a valid e-mail address.") % {"address": address}
                ) from error
        return addresses

    def clean_schedule_cron(self) -> str:
        expression = (self.cleaned_data.get("schedule_cron") or "").strip()
        if not expression:
            return ""
        try:
            parse_cron(expression)
        except CronError as error:
            raise forms.ValidationError(str(error)) from error
        return expression

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_scheduled"):
            # A schedule with nothing to run or nobody to send to is a dead
            # switch that quietly never fires.
            if not cleaned.get("schedule_cron"):
                self.add_error(
                    "schedule_cron", _("A scheduled report needs a schedule expression.")
                )
            if not cleaned.get("recipients"):
                self.add_error(
                    "recipients", _("A scheduled report needs at least one recipient.")
                )
        return cleaned


class GeneratedReportFilterForm(TailwindFormMixin, forms.Form):
    """Filter bar above the export history."""

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Report title or key…")}),
    )
    report_key = forms.ChoiceField(label=_("Report"), choices=(), required=False)
    format = forms.ChoiceField(label=_("Format"), choices=(), required=False)
    status = forms.ChoiceField(label=_("Status"), choices=(), required=False)
    range = forms.ChoiceField(
        label=_("Period"), choices=RANGE_CHOICES, required=False, initial="30"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["report_key"].choices = _blank(
            [(spec.key, spec.title) for spec in all_reports()]
        )
        self.fields["format"].choices = _blank(available_formats())
        self.fields["status"].choices = _blank(ReportStatus.choices)
