"""Student forms."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel
from apps.core.forms_base import TailwindFormMixin
from apps.customers.models import Customer

from .models import SKILL_FIELDS, SkillAssessment, Student
from .selectors import instructor_choices


class StudentForm(TailwindFormMixin, forms.ModelForm):
    """Full student profile. The customer link is fixed once created."""

    class Meta:
        model = Student
        fields = (
            "customer",
            "surf_level",
            "goals",
            "stance",
            "board_preference",
            "can_swim",
            "swim_distance_m",
            "medical_conditions",
            "medications",
            "allergies",
            "weight_kg",
            "height_cm",
            "shoe_size",
            "wetsuit_size",
            "preferred_instructor",
            "joined_at",
            "is_active",
        )
        widgets = {
            "goals": forms.Textarea(attrs={"rows": 3}),
            "medical_conditions": forms.Textarea(attrs={"rows": 2}),
            "medications": forms.Textarea(attrs={"rows": 2}),
            "allergies": forms.Textarea(attrs={"rows": 2}),
            "joined_at": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            # The customer link is identity, not data — changing it would move
            # somebody else's lesson history onto this person.
            self.fields["customer"].disabled = True
            self.fields["customer"].queryset = Customer.all_objects.filter(
                pk=self.instance.customer_id
            )
        else:
            self.fields["customer"].queryset = (
                Customer.objects.active()
                .filter(student_profile__isnull=True)
                .order_by("last_name", "first_name")
            )
            self.fields["customer"].help_text = _(
                "Only customers without a student profile are listed."
            )
        self.fields["swim_distance_m"].widget.attrs["placeholder"] = "25"
        self.fields["weight_kg"].widget.attrs["step"] = "0.5"

    def clean(self):
        cleaned = super().clean()
        # Keep the model-level rule reachable as a field error on the form.
        if cleaned.get("swim_distance_m") and not cleaned.get("can_swim"):
            self.add_error(
                "can_swim", _("A swim distance was entered — confirm the student can swim.")
            )
        return cleaned


class StudentWithCustomerForm(TailwindFormMixin, forms.Form):
    """Register a brand-new person: customer details plus student details."""

    first_name = forms.CharField(label=_("First name"), max_length=80)
    last_name = forms.CharField(label=_("Last name"), max_length=80)
    email = forms.EmailField(label=_("E-mail"), required=False)
    phone = forms.CharField(label=_("Phone"), max_length=32, required=False)
    birth_date = forms.DateField(
        label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    emergency_contact_name = forms.CharField(
        label=_("Emergency contact"), max_length=120, required=False
    )
    emergency_contact_phone = forms.CharField(
        label=_("Emergency contact phone"), max_length=32, required=False
    )
    emergency_contact_relation = forms.CharField(
        label=_("Relationship"), max_length=60, required=False
    )

    surf_level = forms.ChoiceField(
        label=_("Surf level"), choices=SurfLevel.choices, initial=SurfLevel.FIRST_TIME
    )
    can_swim = forms.BooleanField(label=_("Can swim"), required=False)
    swim_distance_m = forms.IntegerField(
        label=_("Swim distance (m)"), required=False, min_value=0, max_value=10000
    )
    goals = forms.CharField(
        label=_("Goals"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    medical_conditions = forms.CharField(
        label=_("Medical conditions"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    allow_duplicate = forms.BooleanField(
        label=_("Save anyway — this is a different person"), required=False
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("email") and not cleaned.get("phone"):
            raise forms.ValidationError(
                _("Enter at least a phone number or an e-mail address.")
            )
        return cleaned


class StudentFilterForm(TailwindFormMixin, forms.Form):
    STATUS_CHOICES = (
        ("", _("All statuses")),
        ("active", _("Active only")),
        ("inactive", _("Archived only")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("Name, student or customer code"), "autocomplete": "off"}
        ),
    )
    level = forms.ChoiceField(label=_("Level"), required=False)
    instructor = forms.ChoiceField(label=_("Preferred instructor"), required=False)
    status = forms.ChoiceField(label=_("Status"), choices=STATUS_CHOICES, required=False)
    needs_assessment = forms.BooleanField(label=_("Never assessed"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["level"].choices = [("", _("Any level"))] + list(SurfLevel.choices)
        self.fields["instructor"].choices = [("", _("Any instructor"))] + [
            (str(pk), label) for pk, label in instructor_choices()
        ]


class SkillAssessmentForm(TailwindFormMixin, forms.ModelForm):
    """Score a student on the five competencies and set the resulting level."""

    class Meta:
        model = SkillAssessment
        fields = (
            "instructor",
            "assessed_on",
            "level_after",
            "paddling",
            "popup",
            "positioning",
            "wave_reading",
            "safety",
            "notes",
            "next_focus",
        )
        widgets = {
            "assessed_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, student: Student | None = None, **kwargs):
        self.student = student
        super().__init__(*args, **kwargs)
        for name in SKILL_FIELDS:
            self.fields[name].widget = forms.NumberInput(
                attrs={"min": 1, "max": 5, "step": 1, "class": "form-input"}
            )
            self.fields[name].required = True
        if student is not None:
            self.fields["level_after"].initial = student.surf_level
            self.fields["level_after"].help_text = _(
                "Current level: %(level)s. A student moves at most one level per assessment."
            ) % {"level": student.get_surf_level_display()}

    def clean(self):
        cleaned = super().clean()
        if self.student is None:
            return cleaned
        level_after = cleaned.get("level_after")
        if level_after:
            from apps.core.enums import level_rank

            from .models import NON_SWIMMER_MAX_LEVEL

            if level_rank(level_after) - level_rank(self.student.surf_level) > 1:
                self.add_error(
                    "level_after", _("A student may only move up one level per assessment.")
                )
            if not self.student.can_swim and level_rank(level_after) > level_rank(
                NON_SWIMMER_MAX_LEVEL
            ):
                self.add_error(
                    "level_after",
                    _("Confirm the student can swim before moving them above this level."),
                )
        return cleaned
