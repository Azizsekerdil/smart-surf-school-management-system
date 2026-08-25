"""Forms for the safety module.

Two of these need explaining:

* ``EvacuationPlanForm`` edits ``steps`` (a JSON list) as one line of text per
  step. Nobody wants to type JSON while a storm is coming in.
* ``EquipmentSafetyCheckForm`` renders the checklist as real checkboxes, one per
  item, and rebuilds the JSON mapping on save — so the stored record says
  exactly which item failed, not just "failed".
* ``WeatherWarningForm`` deliberately cannot create an AI-suggested warning. A
  person typing a warning *is* the authority; AI suggestions arrive from the AI
  module and must be acknowledged through :func:`services.acknowledge_warning`.
"""

from __future__ import annotations

from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import STAFF_ROLES, Role
from apps.core.enums import GenericStatus, Severity
from apps.core.forms_base import INPUT_CLASS, TailwindFormMixin

from .models import (
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)

User = get_user_model()

#: Roles that may be rostered for water safety cover.
LIFEGUARD_ROLES: tuple[str, ...] = (
    Role.LIFEGUARD,
    Role.HEAD_INSTRUCTOR,
    Role.SURF_INSTRUCTOR,
)

#: The default pre-session inspection for surf equipment. Used when a check has
#: no checklist of its own yet; an existing check keeps the items it was made
#: with, so a historical record never changes shape.
DEFAULT_CHECKLIST_ITEMS: tuple[str, ...] = (
    "Leash and leash plug",
    "Fins and fin boxes",
    "Deck and rails — no exposed foam",
    "Nose and tail integrity",
    "No water ingress / delamination",
    "Wetsuit seams and zip",
)


def _use_datetime_widget(form: forms.Form, *names: str) -> None:
    """Swap the named fields to a native ``datetime-local`` picker.

    Done after ``TailwindFormMixin`` has run, so the styling is re-applied here.
    Django's ``forms.DateTimeField`` parses the ISO value the browser submits.
    """
    for name in names:
        field = form.fields[name]
        field.widget = forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": INPUT_CLASS},
            format="%Y-%m-%dT%H:%M",
        )


def _staff_queryset():
    return User.objects.filter(is_active=True, role__in=list(STAFF_ROLES)).order_by(
        "first_name", "last_name", "username"
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class SafetyIncidentForm(TailwindFormMixin, forms.ModelForm):
    """Report or amend an incident."""

    class Meta:
        model = SafetyIncident
        fields = (
            "occurred_at",
            "incident_type",
            "severity",
            "status",
            "spot",
            "lesson",
            "people_involved",
            "staff_involved",
            "description",
            "immediate_action",
            "medical_attention_required",
            "emergency_services_called",
            "photo",
            "follow_up_required",
            "follow_up_due",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "immediate_action": forms.Textarea(attrs={"rows": 3}),
            "people_involved": forms.SelectMultiple(attrs={"size": 6}),
            "staff_involved": forms.SelectMultiple(attrs={"size": 6}),
            "follow_up_due": forms.DateInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        _use_datetime_widget(self, "occurred_at")
        if not self.instance.pk:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )

        self.fields["staff_involved"].queryset = _staff_queryset()
        self.fields["spot"].queryset = self._spot_queryset()
        self.fields["lesson"].queryset = self._lesson_queryset()
        self.fields["people_involved"].queryset = self._student_queryset()

        self.fields["spot"].required = False
        self.fields["lesson"].required = False
        self.fields["lesson"].help_text = _(
            "Link the session this happened during, when there was one."
        )
        self.fields["description"].help_text = _(
            "Facts only: who, what, where, when. The review records the cause."
        )

    # -- querysets kept out of __init__ for readability --------------------
    def _spot_queryset(self):
        from apps.locations.models import SurfSpot

        return SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")

    def _lesson_queryset(self):
        from apps.lessons.models import Lesson

        cutoff = timezone.localdate() - timedelta(days=90)
        return (
            Lesson.objects.filter(date__gte=cutoff)
            .select_related("lesson_type", "spot")
            .order_by("-date", "-start_time")
        )

    def _student_queryset(self):
        from apps.students.models import Student

        return (
            Student.objects.filter(is_active=True)
            .select_related("customer")
            .order_by("customer__first_name", "customer__last_name")
        )

    def clean(self):
        cleaned = super().clean()
        severity = cleaned.get("severity")
        status = cleaned.get("status")
        if (
            severity in (Severity.HIGH, Severity.CRITICAL)
            and status in (GenericStatus.RESOLVED, GenericStatus.CLOSED)
            and not self.instance.reviewed_by_id
        ):
            self.add_error(
                "status",
                _(
                    "A high or critical incident must be reviewed before it is closed. "
                    "Save it as open and use “Review” instead."
                ),
            )
        if cleaned.get("follow_up_required") and not cleaned.get("follow_up_due"):
            self.add_error("follow_up_due", _("Give the follow-up a due date."))
        if cleaned.get("emergency_services_called") and not (
            cleaned.get("immediate_action") or ""
        ).strip():
            self.add_error(
                "immediate_action",
                _("Emergency services were called — record what was done at the scene."),
            )
        return cleaned


class IncidentReviewForm(TailwindFormMixin, forms.Form):
    """The named sign-off that closes an incident."""

    root_cause = forms.CharField(
        label=_("Root cause"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Why it happened, not who is to blame."),
    )
    corrective_action = forms.CharField(
        label=_("Corrective action"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("The concrete change that prevents a repeat."),
    )
    status = forms.ChoiceField(
        label=_("New status"),
        choices=[
            (GenericStatus.IN_PROGRESS, GenericStatus.IN_PROGRESS.label),
            (GenericStatus.ON_HOLD, GenericStatus.ON_HOLD.label),
            (GenericStatus.RESOLVED, GenericStatus.RESOLVED.label),
            (GenericStatus.CLOSED, GenericStatus.CLOSED.label),
        ],
        initial=GenericStatus.RESOLVED,
    )
    follow_up_required = forms.BooleanField(label=_("Follow-up required"), required=False)
    follow_up_due = forms.DateField(
        label=_("Follow-up due"), required=False, widget=forms.DateInput()
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("follow_up_required") and not cleaned.get("follow_up_due"):
            self.add_error("follow_up_due", _("Give the follow-up a due date."))
        return cleaned


class IncidentFilterForm(TailwindFormMixin, forms.Form):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Code, description or cause…")}),
    )
    incident_type = forms.ChoiceField(
        label=_("Type"),
        required=False,
        choices=[("", _("Any type"))] + list(SafetyIncident.IncidentType.choices),
    )
    severity = forms.ChoiceField(
        label=_("Severity"),
        required=False,
        choices=[("", _("Any severity"))] + list(Severity.choices),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("Any status")), ("open", _("Open only"))]
        + list(GenericStatus.choices),
    )


# ---------------------------------------------------------------------------
# Lifeguard cover
# ---------------------------------------------------------------------------
class LifeguardAssignmentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = LifeguardAssignment
        fields = ("spot", "lifeguard", "date", "start_time", "end_time", "is_confirmed", "notes")
        widgets = {
            "date": forms.DateInput(),
            "start_time": forms.TimeInput(),
            "end_time": forms.TimeInput(),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from apps.locations.models import SurfSpot

        self.fields["spot"].queryset = SurfSpot.objects.filter(is_active=True).order_by(
            "-is_primary", "name"
        )
        self.fields["lifeguard"].queryset = User.objects.filter(
            is_active=True, role__in=list(LIFEGUARD_ROLES)
        ).order_by("first_name", "last_name", "username")
        self.fields["lifeguard"].help_text = _(
            "Lifeguards, head instructors and instructors may be rostered for water cover."
        )
        if not self.instance.pk and not self.initial.get("date"):
            self.fields["date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", _("The shift must end after it starts."))
        return cleaned


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------
class EmergencyContactForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = EmergencyContact
        fields = (
            "name",
            "organisation",
            "kind",
            "phone",
            "alternate_phone",
            "address",
            "notes",
            "spot",
            "sort_order",
            "is_active",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "112"}),
            "alternate_phone": forms.TextInput(attrs={"inputmode": "tel"}),
            "sort_order": forms.NumberInput(attrs={"min": "0", "step": "1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.locations.models import SurfSpot

        self.fields["spot"].queryset = SurfSpot.objects.filter(is_active=True).order_by("name")
        self.fields["spot"].required = False
        self.fields["spot"].empty_label = _("Applies to every spot")


# ---------------------------------------------------------------------------
# Evacuation plans
# ---------------------------------------------------------------------------
class EvacuationPlanForm(TailwindFormMixin, forms.ModelForm):
    """``steps`` is edited as plain lines and stored as a JSON list."""

    steps_text = forms.CharField(
        label=_("Steps"),
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=_("One action per line, in the order they are carried out."),
    )

    class Meta:
        model = EvacuationPlan
        fields = (
            "spot",
            "title",
            "trigger_conditions",
            "assembly_point",
            "responsible_role",
            "document",
            "last_drill_date",
            "next_drill_due",
            "is_active",
        )
        widgets = {
            "trigger_conditions": forms.Textarea(attrs={"rows": 3}),
            "last_drill_date": forms.DateInput(),
            "next_drill_due": forms.DateInput(),
        }

    field_order = (
        "spot",
        "title",
        "trigger_conditions",
        "assembly_point",
        "steps_text",
        "responsible_role",
        "document",
        "last_drill_date",
        "next_drill_due",
        "is_active",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.locations.models import SurfSpot

        self.fields["spot"].queryset = SurfSpot.objects.filter(is_active=True).order_by(
            "-is_primary", "name"
        )
        if self.instance.pk and not self.is_bound:
            self.fields["steps_text"].initial = "\n".join(self.instance.step_list)

    def clean_steps_text(self) -> str:
        raw = self.cleaned_data.get("steps_text", "")
        steps = [line.strip() for line in raw.splitlines() if line.strip()]
        if not steps:
            raise forms.ValidationError(_("A plan with no steps is not a plan."))
        if len(steps) > 40:
            raise forms.ValidationError(
                _("Keep the plan to 40 steps or fewer — nobody follows more than that.")
            )
        self._steps = steps
        return raw

    def clean(self):
        cleaned = super().clean()
        steps = getattr(self, "_steps", None)
        if steps is not None:
            # Set it before ``_post_clean`` builds and validates the instance.
            self.instance.steps = steps
        last = cleaned.get("last_drill_date")
        due = cleaned.get("next_drill_due")
        if last and due and due < last:
            self.add_error("next_drill_due", _("The next drill cannot fall before the last one."))
        return cleaned


# ---------------------------------------------------------------------------
# Equipment safety checks
# ---------------------------------------------------------------------------
class EquipmentSafetyCheckForm(TailwindFormMixin, forms.ModelForm):
    """Checklist rendered as real checkboxes; stored as ``{item: passed}``."""

    CHECK_FIELD_PREFIX = "check_"

    class Meta:
        model = EquipmentSafetyCheck
        fields = (
            "equipment",
            "checked_at",
            "passed",
            "issues_found",
            "action_taken",
            "next_check_due",
        )
        widgets = {
            "issues_found": forms.Textarea(attrs={"rows": 3}),
            "action_taken": forms.Textarea(attrs={"rows": 3}),
            "next_check_due": forms.DateInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from apps.equipment.models import Equipment

        self.fields["equipment"].queryset = (
            Equipment.objects.select_related("category").order_by("asset_code", "name")
        )
        _use_datetime_widget(self, "checked_at")
        if not self.instance.pk:
            self.fields["checked_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )

        self.checklist_items = self._items()
        for index, item in enumerate(self.checklist_items):
            existing = (self.instance.checklist or {}).get(item, True)
            self.fields[f"{self.CHECK_FIELD_PREFIX}{index}"] = forms.BooleanField(
                label=item, required=False, initial=bool(existing)
            )
            self.fields[f"{self.CHECK_FIELD_PREFIX}{index}"].widget.attrs["class"] = (
                "h-4 w-4 rounded-sm border-slate-300 text-brand-600 focus:ring-brand-500"
            )

    def _items(self) -> list[str]:
        stored = self.instance.checklist if isinstance(self.instance.checklist, dict) else {}
        return list(stored.keys()) or list(DEFAULT_CHECKLIST_ITEMS)

    @property
    def checklist_fields(self):
        """The checklist boolean fields, for the template to render as a group."""
        return [
            self[f"{self.CHECK_FIELD_PREFIX}{index}"]
            for index in range(len(self.checklist_items))
        ]

    def clean(self):
        cleaned = super().clean()
        checklist = {
            item: bool(cleaned.get(f"{self.CHECK_FIELD_PREFIX}{index}"))
            for index, item in enumerate(self.checklist_items)
        }
        self.instance.checklist = checklist

        any_failed = any(value is False for value in checklist.values())
        passed = cleaned.get("passed")
        if any_failed and passed:
            self.add_error(
                "passed",
                _("An item is ticked as failed — this check cannot be recorded as a pass."),
            )
        if not passed and not (cleaned.get("issues_found") or "").strip():
            self.add_error("issues_found", _("Say what is wrong with the item."))
        return cleaned


# ---------------------------------------------------------------------------
# Weather warnings
# ---------------------------------------------------------------------------
class WeatherWarningForm(TailwindFormMixin, forms.ModelForm):
    """Staff-entered warning. Cannot produce an AI-suggested record."""

    class Meta:
        model = WeatherWarning
        fields = (
            "spot",
            "title",
            "severity",
            "source",
            "description",
            "starts_at",
            "ends_at",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from apps.locations.models import SurfSpot

        self.fields["spot"].queryset = SurfSpot.objects.filter(is_active=True).order_by("name")
        self.fields["spot"].required = False
        self.fields["spot"].empty_label = _("Every spot")

        # A person typing a warning is the authority for it. AI suggestions are
        # created by the AI module and confirmed, never typed here.
        self.fields["source"].choices = [
            (WeatherWarning.Source.MANUAL, WeatherWarning.Source.MANUAL.label),
            (WeatherWarning.Source.PROVIDER, WeatherWarning.Source.PROVIDER.label),
        ]

        _use_datetime_widget(self, "starts_at", "ends_at")
        if not self.instance.pk:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.fields["starts_at"].initial = now
            self.fields["ends_at"].initial = now + timedelta(hours=6)

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", _("The warning must end after it starts."))
        if cleaned.get("source") == WeatherWarning.Source.AI_SUGGESTED:
            self.add_error(
                "source",
                _("AI suggestions are confirmed from the warnings list, not entered here."),
            )
        return cleaned


# ---------------------------------------------------------------------------
# Student restrictions
# ---------------------------------------------------------------------------
class StudentRestrictionForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = StudentRestriction
        fields = (
            "student",
            "restriction_type",
            "description",
            "max_wave_height_m",
            "max_wind_kmh",
            "requires_supervision",
            "cannot_surf",
            "starts_on",
            "ends_on",
            "is_active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "max_wave_height_m": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "max_wind_kmh": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "starts_on": forms.DateInput(),
            "ends_on": forms.DateInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from apps.students.models import Student

        self.fields["student"].queryset = (
            Student.objects.filter(is_active=True)
            .select_related("customer")
            .order_by("customer__first_name", "customer__last_name")
        )
        if not self.instance.pk:
            self.fields["starts_on"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("cannot_surf") and (
            cleaned.get("max_wave_height_m") is not None
            or cleaned.get("max_wind_kmh") is not None
        ):
            self.add_error(
                "cannot_surf",
                _("“Cannot surf” is absolute — clear the wave and wind limits."),
            )
        if (
            not cleaned.get("cannot_surf")
            and not cleaned.get("requires_supervision")
            and cleaned.get("max_wave_height_m") is None
            and cleaned.get("max_wind_kmh") is None
        ):
            self.add_error(
                "description",
                _(
                    "This restriction limits nothing. Set a wave or wind limit, tick "
                    "supervision, or mark the student as unable to surf."
                ),
            )
        starts, ends = cleaned.get("starts_on"), cleaned.get("ends_on")
        if starts and ends and ends < starts:
            self.add_error("ends_on", _("The end date cannot fall before the start date."))
        return cleaned
