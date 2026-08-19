"""Forms for instructor profiles, credentials, availability and absence."""

from __future__ import annotations

import datetime as dt

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import STAFF_ROLES
from apps.core.enums import Language, SurfLevel
from apps.core.forms_base import CHECKBOX_CLASS, BaseModelForm, TailwindFormMixin

from . import services
from .models import (
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)

User = get_user_model()


class CommaSeparatedListField(forms.CharField):
    """A JSON list of short strings, edited as a comma-separated line."""

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [part.strip() for part in str(value).split(",") if part.strip()]


class InstructorForm(BaseModelForm):
    """Create or edit an instructor profile."""

    specialties = CommaSeparatedListField(
        label=_("Specialties"),
        required=False,
        help_text=_("Separate with commas, e.g. longboard, kids, competition."),
    )
    languages = forms.MultipleChoiceField(
        label=_("Languages spoken"),
        required=False,
        choices=Language.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Instructor
        fields = (
            "user",
            "photo",
            "bio",
            "specialties",
            "languages",
            "max_level_taught",
            "max_students_per_lesson",
            "hourly_rate",
            "commission_percent",
            "hire_date",
            "is_active",
            "is_available_for_booking",
            "emergency_contact_name",
            "emergency_contact_phone",
        )
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
            "hire_date": forms.DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only staff accounts that do not already have a profile may be linked.
        candidates = User.objects.filter(role__in=STAFF_ROLES, is_active=True).filter(
            instructor_profile__isnull=True
        )
        if self.instance.pk and self.instance.user_id:
            candidates = User.objects.filter(role__in=STAFF_ROLES).filter(
                Q(instructor_profile__isnull=True) | Q(pk=self.instance.user_id)
            )
        self.fields["user"].queryset = candidates.order_by("first_name", "last_name")
        self.fields["user"].help_text = _(
            "Only active staff accounts without an instructor profile are listed."
        )
        if isinstance(self.initial.get("languages"), str):
            self.initial["languages"] = []
        # The multi-checkbox widget renders one input per choice, so it needs the
        # checkbox styling rather than the text-input styling.
        self.fields["languages"].widget.attrs["class"] = CHECKBOX_CLASS

    def clean_max_students_per_lesson(self):
        value = self.cleaned_data.get("max_students_per_lesson")
        if value is not None and value < 1:
            raise forms.ValidationError(_("An instructor must be able to take one student."))
        return value

    def clean(self):
        cleaned = super().clean()
        level = cleaned.get("max_level_taught")
        maximum = cleaned.get("max_students_per_lesson")
        if level and maximum:
            ceiling = services.ratio_ceiling(level)
            if maximum > ceiling:
                self.add_error(
                    "max_students_per_lesson",
                    _(
                        "The safety ratio for %(level)s allows at most %(max)s students "
                        "per instructor."
                    )
                    % {"level": SurfLevel(level).label, "max": ceiling},
                )
        return cleaned


class CertificationForm(BaseModelForm):
    """Record a credential and its evidence."""

    class Meta:
        model = Certification
        fields = (
            "kind",
            "name",
            "issuing_body",
            "certificate_number",
            "issued_on",
            "expires_on",
            "document",
        )
        widgets = {
            "issued_on": forms.DateInput(),
            "expires_on": forms.DateInput(),
        }

    def __init__(self, *args, instructor: Instructor | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instructor = instructor or getattr(self.instance, "instructor", None)
        self.fields["expires_on"].help_text = _(
            "Leave empty only for a credential that genuinely never expires."
        )

    def clean_certificate_number(self):
        number = (self.cleaned_data.get("certificate_number") or "").strip()
        kind = self.data.get("kind") or self.initial.get("kind")
        if number and self.instructor is not None and kind:
            duplicate = Certification.objects.filter(
                instructor=self.instructor, kind=kind, certificate_number=number
            ).exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError(
                    _("This certificate number is already recorded for this instructor.")
                )
        return number

    def clean(self):
        cleaned = super().clean()
        issued_on = cleaned.get("issued_on")
        expires_on = cleaned.get("expires_on")
        if issued_on and issued_on > timezone.localdate():
            self.add_error("issued_on", _("The issue date cannot be in the future."))
        if issued_on and expires_on and expires_on <= issued_on:
            self.add_error("expires_on", _("The expiry date must be after the issue date."))
        return cleaned


class AvailabilitySlotForm(TailwindFormMixin, forms.Form):
    """One weekly availability window, added from the editor grid."""

    weekday = forms.TypedChoiceField(
        label=_("Weekday"), choices=AvailabilitySlot.Weekday.choices, coerce=int
    )
    start_time = forms.TimeField(label=_("From"), widget=forms.TimeInput())
    end_time = forms.TimeField(label=_("To"), widget=forms.TimeInput())
    valid_from = forms.DateField(label=_("Valid from"), required=False, widget=forms.DateInput())
    valid_until = forms.DateField(label=_("Valid until"), required=False, widget=forms.DateInput())
    is_active = forms.BooleanField(label=_("Active"), required=False, initial=True)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", _("The end time must be after the start time."))
        valid_from = cleaned.get("valid_from")
        valid_until = cleaned.get("valid_until")
        if valid_from and valid_until and valid_until < valid_from:
            self.add_error(
                "valid_until", _("The end of the validity window must not be before its start.")
            )
        return cleaned


class TimeOffForm(BaseModelForm):
    """Request or record an absence."""

    class Meta:
        model = TimeOff
        fields = ("start_date", "end_date", "reason", "note")
        widgets = {
            "start_date": forms.DateInput(),
            "end_date": forms.DateInput(),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, instructor: Instructor | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instructor = instructor or getattr(self.instance, "instructor", None)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", _("The end date must not be before the start date."))
        if start and end and self.instructor is not None:
            clash = services.overlapping_time_off(
                self.instructor, start, end, exclude_pk=self.instance.pk
            ).first()
            if clash is not None:
                self.add_error(
                    "start_date",
                    _("This overlaps an existing absence (%(start)s – %(end)s).")
                    % {"start": clash.start_date, "end": clash.end_date},
                )
        return cleaned


class PerformanceReviewForm(BaseModelForm):
    """Record a periodic appraisal."""

    class Meta:
        model = PerformanceReview
        fields = (
            "period_start",
            "period_end",
            "teaching_quality",
            "punctuality",
            "safety",
            "communication",
            "teamwork",
            "strengths",
            "improvements",
            "goals",
        )
        widgets = {
            "period_start": forms.DateInput(),
            "period_end": forms.DateInput(),
            "strengths": forms.Textarea(attrs={"rows": 3}),
            "improvements": forms.Textarea(attrs={"rows": 3}),
            "goals": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("period_start")
        end = cleaned.get("period_end")
        if start and end and end < start:
            self.add_error("period_end", _("The end of the period must not be before its start."))
        if end and end > timezone.localdate():
            self.add_error("period_end", _("A period that has not finished cannot be reviewed."))
        return cleaned


class AvailabilitySearchForm(TailwindFormMixin, forms.Form):
    """"Who is free?" — the question the phone asks every morning."""

    date = forms.DateField(label=_("Date"), widget=forms.DateInput())
    start_time = forms.TimeField(label=_("From"), widget=forms.TimeInput())
    end_time = forms.TimeField(label=_("To"), widget=forms.TimeInput())
    level = forms.ChoiceField(
        label=_("Group level"),
        required=False,
        choices=[("", _("Any level"))] + list(SurfLevel.choices),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
        self.fields["start_time"].initial = dt.time(10, 0)
        self.fields["end_time"].initial = dt.time(12, 0)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", _("The end time must be after the start time."))
        return cleaned
