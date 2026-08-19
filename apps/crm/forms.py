"""CRM forms.

The segment form deserves a note: an operator never types raw JSON. Each rule is
a real form field, and :meth:`SegmentForm.clean` assembles the whitelisted
``criteria`` document from validated input. That is what keeps the criteria
engine safe — the UI cannot express a rule the engine does not know.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import STAFF_ROLES
from apps.core.enums import SurfLevel
from apps.core.forms_base import TailwindFormMixin

from .models import Campaign, Interaction, Lead, Segment
from .selectors import CRITERIA_SPECS, validate_criteria

DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]


class _DateTimeLocalInput(forms.DateTimeInput):
    """``<input type="datetime-local">``, which only accepts ISO values.

    The browser control ignores a localised value, so the format is pinned here
    rather than left to the active locale — otherwise every edit form would open
    with an empty date.
    """

    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%dT%H:%M")


class _DateInput(forms.DateInput):
    """``<input type="date">`` with the ISO value the control requires."""

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


def _staff_queryset():
    from django.contrib.auth import get_user_model

    return (
        get_user_model()
        .objects.filter(role__in=STAFF_ROLES, is_active=True)
        .order_by("first_name", "last_name")
    )


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
class LeadForm(TailwindFormMixin, forms.ModelForm):
    """Create or edit a lead."""

    next_action_at = forms.DateTimeField(
        label=_("Next action due"),
        required=False,
        input_formats=DATETIME_INPUT_FORMATS,
        widget=_DateTimeLocalInput(),
    )

    class Meta:
        model = Lead
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "source",
            "interest",
            "status",
            "assigned_to",
            "expected_value",
            "probability",
            "next_action",
            "next_action_at",
            "lost_reason",
        )
        widgets = {
            "interest": forms.Textarea(attrs={"rows": 4}),
            "next_action": forms.TextInput(
                attrs={"placeholder": _("e.g. Call back about the August group course")}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _staff_queryset()
        self.fields["assigned_to"].empty_label = _("Unassigned")
        self.fields["last_name"].required = False

        instance = self.instance
        if instance.pk and instance.converted_customer_id:
            # A converted lead is a historical record; only the notes stay open.
            for name in ("status", "expected_value", "probability", "source"):
                self.fields[name].disabled = True

        # Winning happens through the conversion screen, never through a select.
        self.fields["status"].choices = [
            (value, label)
            for value, label in Lead.Status.choices
            if value != Lead.Status.WON or instance.status == Lead.Status.WON
        ]

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_probability(self):
        probability = self.cleaned_data.get("probability")
        if probability is None:
            return Decimal("0.00")
        if probability < 0 or probability > 100:
            raise forms.ValidationError(_("Probability must be between 0 and 100."))
        return probability


class LeadStatusForm(forms.Form):
    """Payload of a kanban card move."""

    status = forms.ChoiceField(choices=Lead.Status.choices)
    lost_reason = forms.CharField(max_length=200, required=False)


class LeadConvertForm(TailwindFormMixin, forms.Form):
    """Convert a lead: create a new customer or link an existing one."""

    MODE_NEW = "new"
    MODE_LINK = "link"

    mode = forms.ChoiceField(
        label=_("Conversion"),
        choices=(
            (MODE_NEW, _("Create a new customer from this lead")),
            (MODE_LINK, _("Link to an existing customer")),
        ),
        initial=MODE_NEW,
        widget=forms.RadioSelect,
    )
    customer = forms.ModelChoiceField(
        label=_("Existing customer"),
        queryset=None,
        required=False,
        empty_label=_("Choose a customer…"),
    )

    def __init__(self, *args, lead: Lead | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lead = lead

        from .selectors import customer_model

        model = customer_model()
        if model is None:  # pragma: no cover - customers is always installed
            self.fields["customer"].queryset = Segment.objects.none()
            self.fields["mode"].choices = [(self.MODE_NEW, _("Create a new customer"))]
        else:
            self.fields["customer"].queryset = model.objects.all()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == self.MODE_LINK and not cleaned.get("customer"):
            self.add_error("customer", _("Choose the customer to link this lead to."))
        return cleaned


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
class InteractionForm(TailwindFormMixin, forms.ModelForm):
    """Log a call, e-mail, visit, review or complaint."""

    occurred_at = forms.DateTimeField(
        label=_("Occurred at"),
        input_formats=DATETIME_INPUT_FORMATS,
        widget=_DateTimeLocalInput(),
    )
    follow_up_at = forms.DateTimeField(
        label=_("Follow-up due"),
        required=False,
        input_formats=DATETIME_INPUT_FORMATS,
        widget=_DateTimeLocalInput(),
    )

    class Meta:
        model = Interaction
        fields = (
            "customer",
            "lead",
            "kind",
            "direction",
            "subject",
            "body",
            "occurred_at",
            "duration_minutes",
            "follow_up_required",
            "follow_up_at",
            "sentiment",
        )
        widgets = {
            "customer": forms.HiddenInput(),
            "lead": forms.HiddenInput(),
            "body": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if not self.initial.get("occurred_at") and not self.instance.pk:
            self.initial["occurred_at"] = timezone.localtime().replace(second=0, microsecond=0)
        self.fields["sentiment"].required = False
        self.fields["sentiment"].choices = [
            ("", _("Not assessed")),
            *Interaction.Sentiment.choices,
        ]

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("customer") and not cleaned.get("lead"):
            raise forms.ValidationError(
                _("Attach the interaction to a customer or to a lead.")
            )
        return cleaned


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
TRI_STATE = (
    ("", _("Any")),
    ("true", _("Yes")),
    ("false", _("No")),
)


def _split_list(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


class SegmentForm(TailwindFormMixin, forms.ModelForm):
    """Build a whitelisted audience definition without typing JSON."""

    surf_level = forms.MultipleChoiceField(
        label=_("Surf level"),
        choices=SurfLevel.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    marketing_consent = forms.ChoiceField(
        label=_("Marketing consent"), choices=TRI_STATE, required=False
    )
    has_email = forms.ChoiceField(label=_("Has e-mail"), choices=TRI_STATE, required=False)
    has_phone = forms.ChoiceField(label=_("Has phone"), choices=TRI_STATE, required=False)
    city = forms.CharField(
        label=_("Cities"),
        required=False,
        help_text=_("Comma-separated, e.g. Çeşme, İzmir"),
    )
    country = forms.CharField(
        label=_("Countries"),
        required=False,
        help_text=_("Comma-separated ISO codes or names, e.g. TR, DE"),
    )
    language = forms.CharField(
        label=_("Languages"),
        required=False,
        help_text=_("Comma-separated language codes, e.g. tr, en"),
    )
    tags = forms.CharField(
        label=_("Tags"),
        required=False,
        help_text=_("Comma-separated tag slugs."),
    )
    created_within_days = forms.IntegerField(
        label=_("Added within (days)"), required=False, min_value=1, max_value=3650
    )
    min_lifetime_value = forms.DecimalField(
        label=_("Minimum lifetime value"),
        required=False,
        min_value=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
    )
    min_bookings = forms.IntegerField(
        label=_("Minimum bookings"), required=False, min_value=0, max_value=1000
    )
    max_bookings = forms.IntegerField(
        label=_("Maximum bookings"), required=False, min_value=0, max_value=1000
    )
    last_visit_days = forms.IntegerField(
        label=_("Visited within (days)"), required=False, min_value=1, max_value=3650
    )
    no_visit_days = forms.IntegerField(
        label=_("Not seen for (days)"),
        required=False,
        min_value=1,
        max_value=3650,
        help_text=_("Use this to build a win-back audience."),
    )

    class Meta:
        model = Segment
        fields = ("name", "description", "is_dynamic")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    #: Form field -> criteria key, for the fields that map one-to-one.
    LIST_FIELDS = ("city", "country", "language", "tags")
    NUMBER_FIELDS = (
        "created_within_days",
        "min_lifetime_value",
        "min_bookings",
        "max_bookings",
        "last_visit_days",
        "no_visit_days",
    )
    TRI_FIELDS = ("marketing_consent", "has_email", "has_phone")

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self._load_criteria(self.instance.criteria or {})
        # Rules whose backing data does not exist are shown but flagged.
        self.unavailable_rules = [
            str(spec["label"]) for spec in CRITERIA_SPECS.values() if not spec["available"]()
        ]

    def _load_criteria(self, criteria: dict) -> None:
        """Populate the individual widgets from the stored JSON."""
        value = criteria.get("surf_level")
        if isinstance(value, list):
            self.initial["surf_level"] = value

        for key in self.TRI_FIELDS:
            if key in criteria:
                self.initial[key] = "true" if criteria[key] else "false"

        for key in self.LIST_FIELDS:
            value = criteria.get(key)
            if isinstance(value, list):
                self.initial[key] = ", ".join(str(item) for item in value)

        for key in self.NUMBER_FIELDS:
            if key in criteria:
                self.initial[key] = criteria[key]

    def build_criteria(self, cleaned: dict) -> dict:
        """Assemble the criteria document from cleaned form data."""
        criteria: dict = {}

        levels = cleaned.get("surf_level") or []
        if levels:
            criteria["surf_level"] = list(levels)

        for key in self.TRI_FIELDS:
            raw = cleaned.get(key)
            if raw in ("true", "false"):
                criteria[key] = raw == "true"

        for key in self.LIST_FIELDS:
            values = _split_list(cleaned.get(key, ""))
            if values:
                criteria[key] = values

        for key in self.NUMBER_FIELDS:
            value = cleaned.get(key)
            if value not in (None, ""):
                criteria[key] = float(value) if key == "min_lifetime_value" else int(value)

        return criteria

    def clean(self):
        cleaned = super().clean()
        criteria = self.build_criteria(cleaned)

        if not criteria:
            raise forms.ValidationError(
                _("Add at least one rule — an empty segment would target every customer.")
            )

        problems = validate_criteria(criteria)
        if problems:
            raise forms.ValidationError(problems)

        low, high = cleaned.get("min_bookings"), cleaned.get("max_bookings")
        if low is not None and high is not None and low > high:
            self.add_error("max_bookings", _("The maximum cannot be below the minimum."))

        if cleaned.get("last_visit_days") and cleaned.get("no_visit_days"):
            self.add_error(
                "no_visit_days",
                _("“Visited within” and “Not seen for” contradict each other."),
            )

        self.criteria = criteria
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.criteria = getattr(self, "criteria", {})
        if commit:
            instance.save()
        return instance


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
class CampaignForm(TailwindFormMixin, forms.ModelForm):
    """Plan a campaign and, once it has run, record what it achieved."""

    start_date = forms.DateField(
        label=_("Start date"), input_formats=DATE_INPUT_FORMATS, widget=_DateInput()
    )
    end_date = forms.DateField(
        label=_("End date"), input_formats=DATE_INPUT_FORMATS, widget=_DateInput()
    )

    class Meta:
        model = Campaign
        fields = (
            "name",
            "code",
            "channel",
            "status",
            "start_date",
            "end_date",
            "target_segment",
            "budget",
            "actual_spend",
            "message_subject",
            "message_body",
            "sent_count",
            "opened_count",
            "converted_count",
            "revenue_attributed",
        )
        widgets = {"message_body": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        self.fields["target_segment"].queryset = Segment.objects.all().order_by("name")
        self.fields["target_segment"].empty_label = _("No segment (manual audience)")
        if not self.instance.pk:
            self.initial.setdefault("start_date", timezone.localdate())
            # A campaign can only start as a draft or already scheduled.
            self.fields["status"].choices = [
                (value, label)
                for value, label in Campaign.Status.choices
                if value in Campaign.EDITABLE_STATUSES
            ]
        else:
            allowed = {self.instance.status, *self.instance.allowed_next_statuses()}
            self.fields["status"].choices = [
                (value, label)
                for value, label in Campaign.Status.choices
                if value in allowed
            ]

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        return code

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", _("The end date cannot be before the start date."))
        return cleaned


class CampaignStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Campaign.Status.choices)
