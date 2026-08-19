"""Forms for camps, participants, days and activities."""

from __future__ import annotations

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import BaseModelForm, TailwindFormMixin

from .models import (
    CampActivity,
    CampDay,
    CampParticipant,
    CampStatus,
    ParticipantStatus,
    SurfCamp,
)
from .selectors import students_on_camp_ids

DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"]


class DateTimeLocalInput(forms.DateTimeInput):
    """``<input type="datetime-local">`` that round-trips an existing value."""

    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%dT%H:%M")


class SurfCampForm(BaseModelForm):
    """Create and edit the camp product itself."""

    class Meta:
        model = SurfCamp
        fields = [
            "name",
            "code",
            "description",
            "photo",
            "start_date",
            "end_date",
            "spot",
            "capacity",
            "min_participants",
            "min_level",
            "max_level",
            "price",
            "deposit_amount",
            "single_room_supplement",
            "includes_accommodation",
            "includes_meals",
            "includes_transfer",
            "includes_equipment",
            "includes_insurance",
            "accommodation_name",
            "accommodation_address",
            "meal_plan",
            "transfer_pickup_point",
            "transfer_notes",
            "status",
            "lead_instructor",
            "instructors",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "accommodation_address": forms.Textarea(attrs={"rows": 3}),
            "meal_plan": forms.Textarea(attrs={"rows": 3}),
            "transfer_notes": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(),
            "end_date": forms.DateInput(),
            "instructors": forms.SelectMultiple(attrs={"size": 8}),
        }
        help_texts = {
            "capacity": _("Total places, including the ones already sold."),
            "instructors": _("Everyone teaching on this camp. Hold Ctrl to pick several."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        self.fields["spot"].empty_label = _("Select a surf spot")
        self.fields["lead_instructor"].empty_label = _("Not assigned yet")
        if not self.instance.pk:
            today = timezone.localdate()
            self.fields["start_date"].initial = self.fields["start_date"].initial or today

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            return ""
        clash = SurfCamp.all_objects.filter(code=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("This camp code is already in use."))
        return code

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        start = cleaned.get("start_date")
        if (
            status in (CampStatus.PUBLISHED, CampStatus.FULL)
            and not self.instance.pk
            and start
            and start < timezone.localdate()
        ):
            self.add_error(
                "status", _("A camp starting in the past cannot be created as published.")
            )
        return cleaned


class CampParticipantForm(BaseModelForm):
    """Register someone on a camp, or edit their logistics afterwards."""

    class Meta:
        model = CampParticipant
        fields = [
            "student",
            "booking",
            "room_type",
            "room_number",
            "roommate_preference",
            "arrival_datetime",
            "arrival_flight",
            "departure_datetime",
            "departure_flight",
            "needs_transfer",
            "dietary_requirements",
            "medical_notes",
            "t_shirt_size",
            "amount_paid",
            "deposit_paid",
        ]
        widgets = {
            "medical_notes": forms.Textarea(attrs={"rows": 3}),
            "arrival_datetime": DateTimeLocalInput(),
            "departure_datetime": DateTimeLocalInput(),
        }
        help_texts = {
            "medical_notes": _("Allergies, medication, conditions — shown on the daily roster."),
            "amount_paid": _("Total collected so far for this place."),
        }

    def __init__(self, *args, camp: SurfCamp | None = None, **kwargs):
        self.camp = camp
        super().__init__(*args, **kwargs)

        for field_name in ("arrival_datetime", "departure_datetime"):
            self.fields[field_name].input_formats = DATETIME_INPUT_FORMATS

        self.fields["booking"].required = False
        self.fields["booking"].empty_label = _("No booking linked")
        self.fields["student"].empty_label = _("Select a student")

        if camp is not None:
            taken = students_on_camp_ids(camp)
            if self.instance.pk:
                taken = [pk for pk in taken if pk != self.instance.student_id]
            self.fields["student"].queryset = self.fields["student"].queryset.exclude(
                pk__in=taken
            )
            self.fields["arrival_datetime"].help_text = _("Camp runs %(start)s – %(end)s.") % {
                "start": camp.start_date,
                "end": camp.end_date,
            }

        if self.instance.pk:
            # The student is fixed once a place exists: cancel and re-register
            # instead, so history and money stay attached to the right person.
            self.fields["student"].disabled = True

    def clean(self):
        cleaned = super().clean()
        camp = self.camp or getattr(self.instance, "camp", None)
        arrival = cleaned.get("arrival_datetime")
        departure = cleaned.get("departure_datetime")

        if arrival and departure and departure < arrival:
            self.add_error("departure_datetime", _("Departure cannot be before arrival."))

        if camp is not None and camp.start_date and camp.end_date:
            if arrival and timezone.localtime(arrival).date() > camp.end_date:
                self.add_error("arrival_datetime", _("Arrival is after the camp has finished."))
            if departure and timezone.localtime(departure).date() < camp.start_date:
                self.add_error(
                    "departure_datetime", _("Departure is before the camp starts.")
                )
        return cleaned


class ParticipantStatusForm(TailwindFormMixin, forms.Form):
    """Check-in / check-out / confirm from the participants table."""

    status = forms.ChoiceField(label=_("Status"), choices=ParticipantStatus.choices)


class CancellationForm(TailwindFormMixin, forms.Form):
    """Reason captured whenever a place or a whole camp is called off."""

    reason = forms.CharField(
        label=_("Reason"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Why is this being cancelled?")}),
    )


class CampDayForm(BaseModelForm):
    class Meta:
        model = CampDay
        fields = ["date", "day_number", "title", "description", "weather_note", "spot"]
        widgets = {
            "date": forms.DateInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
            "weather_note": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {"spot": _("Override the camp's home spot for this day only.")}

    def __init__(self, *args, camp: SurfCamp | None = None, **kwargs):
        self.camp = camp
        super().__init__(*args, **kwargs)
        self.fields["spot"].empty_label = _("Use the camp's home spot")
        if camp is not None:
            self.fields["date"].help_text = _("Between %(start)s and %(end)s.") % {
                "start": camp.start_date,
                "end": camp.end_date,
            }

    def clean_date(self):
        day_date = self.cleaned_data["date"]
        camp = self.camp or getattr(self.instance, "camp", None)
        if camp is not None and camp.start_date and camp.end_date:
            if not (camp.start_date <= day_date <= camp.end_date):
                raise forms.ValidationError(_("The date must fall inside the camp's dates."))
            clash = CampDay.objects.filter(camp=camp, date=day_date)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError(_("This camp already has a day on that date."))
        return day_date


class CampActivityForm(BaseModelForm):
    class Meta:
        model = CampActivity
        fields = [
            "start_time",
            "end_time",
            "title",
            "activity_type",
            "instructor",
            "lesson",
            "location",
            "notes",
        ]
        widgets = {
            "start_time": forms.TimeInput(),
            "end_time": forms.TimeInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, camp_day: CampDay | None = None, **kwargs):
        self.camp_day = camp_day
        super().__init__(*args, **kwargs)
        self.fields["instructor"].required = False
        self.fields["instructor"].empty_label = _("No instructor")
        self.fields["lesson"].required = False
        self.fields["lesson"].empty_label = _("Not linked to a lesson")

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", _("The end time must be after the start time."))
        return cleaned


class CampFilterForm(TailwindFormMixin, forms.Form):
    """Filters above the camp list."""

    STATUS_CHOICES = (("", _("All statuses")), *CampStatus.choices)
    PERIOD_CHOICES = (
        ("upcoming", _("Upcoming")),
        ("running", _("Running now")),
        ("past", _("Finished")),
        ("all", _("All camps")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Name, code or accommodation")}),
    )
    status = forms.ChoiceField(label=_("Status"), choices=STATUS_CHOICES, required=False)
    period = forms.ChoiceField(
        label=_("Period"), choices=PERIOD_CHOICES, required=False, initial="upcoming"
    )
