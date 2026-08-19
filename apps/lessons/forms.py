"""Forms for the lesson catalogue, the timetable and the roster."""

from __future__ import annotations

from datetime import datetime

from django import forms
from django.apps import apps as django_apps
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import LessonStatus, SurfLevel
from apps.core.forms_base import TailwindFormMixin

from .models import Lesson, LessonAttendance, LessonType
from .services import available_equipment, check_lesson_conflicts, check_lesson_warnings


def _model_or_none(app_label: str, model_name: str):
    try:
        return django_apps.get_model(app_label, model_name)
    except LookupError:  # pragma: no cover - only when an app is not installed
        return None


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class LessonTypeForm(TailwindFormMixin, forms.ModelForm):
    """Create / edit a sellable lesson product."""

    class Meta:
        model = LessonType
        fields = [
            "code",
            "name",
            "category",
            "description",
            "min_level",
            "max_level",
            "min_age",
            "max_age",
            "duration_minutes",
            "min_students",
            "max_students",
            "base_price",
            "price_per_extra_student",
            "requires_board",
            "requires_wetsuit",
            "requires_leash",
            "colour",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "colour": forms.TextInput(attrs={"type": "color"}),
        }

    def clean_code(self) -> str:
        return (self.cleaned_data.get("code") or "").strip().upper()


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------
class LessonForm(TailwindFormMixin, forms.ModelForm):
    """Schedule or reschedule a lesson.

    Field-level validation is the model's job; the cross-record rules (who is
    already teaching, how many people the spot holds, what the safety ratio
    allows) come from ``services.check_lesson_conflicts`` so the form, the API
    and the admin all refuse exactly the same things.
    """

    class Meta:
        model = Lesson
        fields = [
            "lesson_type",
            "spot",
            "date",
            "start_time",
            "end_time",
            "instructor",
            "assistant_instructors",
            "capacity",
            "status",
            "price_override",
            "notes",
            "internal_notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.warnings: list[str] = []
        self.fields["lesson_type"].queryset = LessonType.objects.filter(is_active=True).order_by(
            "sort_order", "name"
        )
        self.fields["assistant_instructors"].required = False
        self.fields["price_override"].required = False
        # Cancelling is a deliberate, reason-bearing action with its own screen,
        # so it is never just another value in this dropdown.
        self.fields["status"].choices = [
            (value, label)
            for value, label in LessonStatus.choices
            if value != LessonStatus.CANCELLED or self.instance.status == LessonStatus.CANCELLED
        ]
        self.fields["capacity"].help_text = _(
            "Suggested from the lesson type and the number of instructors on the water."
        )
        if not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned

        proposal = {
            "lesson_type": cleaned.get("lesson_type"),
            "spot": cleaned.get("spot"),
            "date": cleaned.get("date"),
            "start_time": cleaned.get("start_time"),
            "end_time": cleaned.get("end_time"),
            "instructor": cleaned.get("instructor"),
            "assistant_instructors": list(cleaned.get("assistant_instructors") or []),
            "capacity": cleaned.get("capacity"),
            "status": cleaned.get("status") or LessonStatus.SCHEDULED,
        }
        conflicts = check_lesson_conflicts(proposal, exclude_pk=self.instance.pk)
        for message in conflicts:
            self.add_error(None, message)
        self.warnings = check_lesson_warnings(proposal, exclude_pk=self.instance.pk)
        return cleaned


class LessonConflictCheckForm(forms.Form):
    """Loose mirror of :class:`LessonForm` used by the live conflict endpoint.

    Every field is optional: the user is still filling the form in, so the
    endpoint answers with whatever can be checked so far.
    """

    lesson_type = forms.ModelChoiceField(queryset=LessonType.objects.all(), required=False)
    date = forms.DateField(required=False)
    start_time = forms.TimeField(required=False)
    end_time = forms.TimeField(required=False)
    capacity = forms.IntegerField(required=False, min_value=1)
    lesson_id = forms.IntegerField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Instructor = _model_or_none("instructors", "Instructor")
        SurfSpot = _model_or_none("locations", "SurfSpot")
        self.fields["instructor"] = forms.ModelChoiceField(
            queryset=Instructor.objects.all() if Instructor else LessonType.objects.none(),
            required=False,
        )
        self.fields["assistant_instructors"] = forms.ModelMultipleChoiceField(
            queryset=Instructor.objects.all() if Instructor else LessonType.objects.none(),
            required=False,
        )
        self.fields["spot"] = forms.ModelChoiceField(
            queryset=SurfSpot.objects.all() if SurfSpot else LessonType.objects.none(),
            required=False,
        )

    def as_proposal(self) -> dict:
        data = self.cleaned_data
        return {
            "lesson_type": data.get("lesson_type"),
            "spot": data.get("spot"),
            "date": data.get("date"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "instructor": data.get("instructor"),
            "assistant_instructors": list(data.get("assistant_instructors") or []),
            "capacity": data.get("capacity") or 0,
            "status": LessonStatus.SCHEDULED,
        }


class LessonFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the lesson list."""

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Lesson code, notes…")}),
    )
    status = forms.ChoiceField(label=_("Status"), required=False, choices=[])
    lesson_type = forms.ModelChoiceField(
        label=_("Lesson type"),
        queryset=LessonType.objects.all(),
        required=False,
        empty_label=_("All lesson types"),
    )
    level = forms.ChoiceField(label=_("Level"), required=False, choices=[])
    start = forms.DateField(label=_("From"), required=False)
    end = forms.DateField(label=_("To"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("", _("All statuses"))] + list(LessonStatus.choices)
        self.fields["level"].choices = [("", _("All levels"))] + list(SurfLevel.choices)

        Instructor = _model_or_none("instructors", "Instructor")
        SurfSpot = _model_or_none("locations", "SurfSpot")
        self.fields["instructor"] = forms.ModelChoiceField(
            label=_("Instructor"),
            queryset=Instructor.objects.all() if Instructor else LessonType.objects.none(),
            required=False,
            empty_label=_("All instructors"),
        )
        self.fields["spot"] = forms.ModelChoiceField(
            label=_("Surf spot"),
            queryset=SurfSpot.objects.all() if SurfSpot else LessonType.objects.none(),
            required=False,
            empty_label=_("All spots"),
        )
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} form-input".strip()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            cleaned["start"], cleaned["end"] = end, start
        return cleaned


# ---------------------------------------------------------------------------
# Operational actions
# ---------------------------------------------------------------------------
class LessonCancelForm(TailwindFormMixin, forms.Form):
    reason = forms.CharField(
        label=_("Reason for cancellation"),
        widget=forms.Textarea(attrs={"rows": 3, "autofocus": True}),
        max_length=1000,
        help_text=_(
            "Recorded on the audit trail and passed to every affected booking."
        ),
    )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError(_("Please give a usable reason (at least 5 characters)."))
        return reason


class LessonCompleteForm(TailwindFormMixin, forms.Form):
    mark_unchecked_as_no_show = forms.BooleanField(
        label=_("Mark students who never checked in as no-shows"),
        required=False,
        initial=False,
    )


class AddAttendeeForm(TailwindFormMixin, forms.Form):
    """Add one student to a lesson from the roster screen."""

    def __init__(self, *args, lesson: Lesson | None = None, **kwargs):
        self.lesson = lesson
        super().__init__(*args, **kwargs)
        Student = _model_or_none("students", "Student")
        Booking = _model_or_none("bookings", "Booking")
        student_qs = Student.objects.all() if Student else LessonType.objects.none()
        if lesson is not None and Student is not None:
            already = lesson.attendances.filter(
                is_deleted=False, status__in=LessonAttendance.SEAT_TAKING_STATUSES
            ).values_list("student_id", flat=True)
            student_qs = student_qs.exclude(pk__in=list(already))
        self.fields["student"] = forms.ModelChoiceField(
            label=_("Student"), queryset=student_qs, required=True
        )
        self.fields["booking"] = forms.ModelChoiceField(
            label=_("Booking"),
            queryset=Booking.objects.all() if Booking else LessonType.objects.none(),
            required=False,
            empty_label=_("No booking (walk-in)"),
        )
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} form-select".strip()


class AssignEquipmentForm(TailwindFormMixin, forms.Form):
    """Hand a board and a wetsuit to one student.

    The choices are the items that are free for this lesson's whole time
    window — kit already out on an overlapping lesson never appears.
    """

    def __init__(self, *args, attendance: LessonAttendance, pool=None, **kwargs):
        self.attendance = attendance
        super().__init__(*args, **kwargs)
        # ``pool`` lets the roster screen compute the free-kit list once for the
        # whole table instead of once per student.
        if pool is None:
            pool = available_equipment(attendance.lesson)
        Equipment = _model_or_none("equipment", "Equipment")
        if Equipment is None:
            queryset = LessonType.objects.none()
        else:
            free_pks = [item.pk for item in pool]
            current = [
                pk
                for pk in (attendance.assigned_board_id, attendance.assigned_wetsuit_id)
                if pk
            ]
            queryset = Equipment.objects.filter(pk__in=free_pks + current)
        self.fields["board"] = forms.ModelChoiceField(
            label=_("Board"),
            queryset=queryset,
            required=False,
            empty_label=_("No board"),
            initial=attendance.assigned_board_id,
        )
        self.fields["wetsuit"] = forms.ModelChoiceField(
            label=_("Wetsuit"),
            queryset=queryset,
            required=False,
            empty_label=_("No wetsuit"),
            initial=attendance.assigned_wetsuit_id,
        )
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} form-select".strip()

    def clean(self):
        cleaned = super().clean()
        board, wetsuit = cleaned.get("board"), cleaned.get("wetsuit")
        if board and wetsuit and board.pk == wetsuit.pk:
            raise forms.ValidationError(
                _("The same item cannot be issued as both the board and the wetsuit.")
            )
        return cleaned


class AttendanceFeedbackForm(TailwindFormMixin, forms.ModelForm):
    """Instructor notes and the student's rating, captured after the lesson."""

    class Meta:
        model = LessonAttendance
        fields = ["rating", "student_feedback", "instructor_notes"]
        widgets = {
            "student_feedback": forms.Textarea(attrs={"rows": 2}),
            "instructor_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rating"] = forms.TypedChoiceField(
            label=_("Student rating"),
            required=False,
            coerce=int,
            empty_value=None,
            choices=[("", _("Not rated"))] + [(n, f"{n} / 5") for n in range(1, 6)],
            initial=self.instance.rating,
        )
        existing = self.fields["rating"].widget.attrs.get("class", "")
        self.fields["rating"].widget.attrs["class"] = f"{existing} form-select".strip()


class DayPickerForm(TailwindFormMixin, forms.Form):
    """The single date input on the day view."""

    day = forms.DateField(label=_("Day"), required=False)

    def selected_day(self):
        return self.cleaned_data.get("day") if self.is_valid() else None

    @staticmethod
    def parse(raw: str | None):
        if not raw:
            return timezone.localdate()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return timezone.localdate()
