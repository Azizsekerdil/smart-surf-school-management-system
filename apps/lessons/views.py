"""HTML views for the timetable, the roster and the lesson catalogue.

Views orchestrate only. Every decision that can refuse an action — ratios,
availability, capacity, equipment, state transitions — is delegated to
:mod:`apps.lessons.services`, so the API and the admin behave identically.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from apps.accounts.permissions import CapabilityRequiredMixin, StaffOnlyMixin
from apps.accounts.scoping import SHARED
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
)

from .forms import (
    AddAttendeeForm,
    AssignEquipmentForm,
    AttendanceFeedbackForm,
    DayPickerForm,
    LessonCancelForm,
    LessonCompleteForm,
    LessonConflictCheckForm,
    LessonFilterForm,
    LessonForm,
    LessonTypeForm,
)
from .models import Lesson, LessonAttendance, LessonType
from .selectors import (
    BOOKED_ANNOTATION,
    day_summary,
    lessons_on,
    roster_queryset,
)
from .services import (
    add_student_to_lesson,
    assign_equipment_to_attendance,
    available_equipment,
    cancel_lesson,
    capture_conditions_snapshot,
    check_in_student,
    check_lesson_conflicts,
    check_lesson_warnings,
    complete_lesson,
    lessons_for_calendar,
    mark_no_show,
    mark_safety_check,
    remove_student_from_lesson,
    suggest_capacity,
)

#: How far the default list window reaches around today.
DEFAULT_PAST_DAYS = 7
DEFAULT_FUTURE_DAYS = 30


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------
class LessonListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    """Filterable list of lessons.

    Customers and students see only the lessons they are actually on — the
    owner scope is applied before any filter, never after.
    """

    capability = "lessons.view"
    model = Lesson
    template_name = "lessons/lesson_list.html"
    partial_template_name = "lessons/partials/lesson_table.html"
    context_object_name = "lessons"
    paginate_by = 25
    # Student has no ``user`` of its own; it reaches one through its Customer.
    owner_lookup = "attendances__student__customer__user"
    search_fields = ("lesson_code", "notes", "lesson_type__name", "spot__name")

    def get_filter_form(self) -> LessonFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = LessonFilterForm(self.request.GET or None)
            self._filter_form.is_valid()
        return self._filter_form

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("lesson_type", "spot", "instructor")
            .annotate(booked=BOOKED_ANNOTATION)
        )
        form = self.get_filter_form()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}

        today = timezone.localdate()
        start = data.get("start") or (today - timedelta(days=DEFAULT_PAST_DAYS))
        end = data.get("end") or (today + timedelta(days=DEFAULT_FUTURE_DAYS))
        queryset = queryset.filter(date__gte=start, date__lte=end)

        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("lesson_type"):
            queryset = queryset.filter(lesson_type=data["lesson_type"])
        if data.get("spot"):
            queryset = queryset.filter(spot=data["spot"])
        if data.get("instructor"):
            queryset = queryset.filter(
                Q(instructor=data["instructor"]) | Q(assistant_instructors=data["instructor"])
            )
        if data.get("level"):
            queryset = queryset.filter(
                lesson_type__min_level__lte=data["level"], lesson_type__max_level__gte=data["level"]
            )
        self.range_start, self.range_end = start, end
        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        context["range_start"] = getattr(self, "range_start", None)
        context["range_end"] = getattr(self, "range_end", None)
        context["today"] = timezone.localdate()
        return context


class LessonDayView(CapabilityRequiredMixin, StaffOnlyMixin, TemplateView):
    """One day of the timetable, ordered by start time.

    This is the screen the desk keeps open all morning: who is teaching what,
    where, how full it is and whether the safety briefing has been signed off.

    It is a whole-school run sheet with no own-rows projection, so it is
    closed to customers and students even though they hold ``lessons.view``.
    """

    capability = "lessons.view"
    template_name = "lessons/lesson_day.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        day = DayPickerForm.parse(self.request.GET.get("day"))

        instructor = spot = None
        filter_form = LessonFilterForm(self.request.GET or None)
        if filter_form.is_bound and filter_form.is_valid():
            instructor = filter_form.cleaned_data.get("instructor")
            spot = filter_form.cleaned_data.get("spot")

        lessons = list(lessons_on(day, instructor=instructor, spot=spot))

        # A one-week strip around the selected day, so the desk can jump
        # between days without leaving the screen.
        week_start = day - timedelta(days=day.weekday())
        week_events = lessons_for_calendar(
            week_start, week_start + timedelta(days=6), instructor=instructor, spot=spot
        )
        week_days = []
        for offset in range(7):
            current = week_start + timedelta(days=offset)
            key = current.isoformat()
            week_days.append(
                {
                    "date": current,
                    "count": sum(1 for event in week_events if event["date"] == key),
                    "is_selected": current == day,
                    "is_today": current == timezone.localdate(),
                }
            )

        context.update(
            {
                "day": day,
                "summary": day_summary(day),
                "lessons": lessons,
                "events": lessons_for_calendar(day, day, instructor=instructor, spot=spot),
                "week_days": week_days,
                "filter_form": filter_form,
                "day_form": DayPickerForm(initial={"day": day}),
                "previous_day": day - timedelta(days=1),
                "next_day": day + timedelta(days=1),
                "today": timezone.localdate(),
            }
        )
        return context


class LessonDetailView(CapabilityRequiredMixin, OwnerScopedQuerysetMixin, DetailView):
    capability = "lessons.view"
    model = Lesson
    template_name = "lessons/lesson_detail.html"
    context_object_name = "lesson"
    owner_lookup = "attendances__student__customer__user"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("lesson_type", "spot", "instructor", "safety_checked_by")
            .prefetch_related("assistant_instructors")
            .distinct()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(roster_context(self.object))
        context["conditions"] = sorted(
            (key, value)
            for key, value in (self.object.conditions_snapshot or {}).items()
            if key not in {"source"}
        )
        # Conflicts are only meaningful while the lesson can still be changed.
        if self.object.is_editable and not self.object.is_past:
            context["warnings"] = check_lesson_warnings(self.object)
            context["conflicts"] = check_lesson_conflicts(self.object)
        else:
            context["warnings"] = []
            context["conflicts"] = []
        return context


def roster_context(lesson: Lesson, error: str = "") -> dict:
    """Everything the roster panel needs, shared by the page and the HTMX swaps."""
    attendances = list(roster_queryset(lesson))
    # One equipment query for the whole table, not one per student.
    pool = list(available_equipment(lesson))
    return {
        "lesson": lesson,
        "attendances": attendances,
        "equipment_forms": {
            attendance.pk: AssignEquipmentForm(attendance=attendance, pool=pool)
            for attendance in attendances
            if attendance.status != LessonAttendance.Status.CANCELLED
        },
        "feedback_forms": {
            attendance.pk: AttendanceFeedbackForm(instance=attendance)
            for attendance in attendances
        },
        "add_form": AddAttendeeForm(lesson=lesson),
        "roster_error": error,
    }


def render_roster(request, lesson: Lesson, error: str = "") -> HttpResponse:
    """Render the roster panel as an HTMX fragment.

    Always answers 200: htmx only swaps successful responses, and a refused
    action still needs to show the user *why* it was refused.
    """
    return render(
        request,
        "lessons/partials/roster.html",
        roster_context(lesson, error=error),
    )


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
class LessonCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "lessons.add"
    model = Lesson
    form_class = LessonForm
    template_name = "lessons/lesson_form.html"
    success_message = _("Lesson scheduled.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Schedule a lesson")
        context["warnings"] = getattr(context.get("form"), "warnings", [])
        return context

    def get_success_url(self):
        return reverse("lessons:detail", kwargs={"pk": self.object.pk})


class LessonUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "lessons.change"
    model = Lesson
    form_class = LessonForm
    template_name = "lessons/lesson_form.html"
    context_object_name = "lesson"
    success_message = _("Lesson updated.")

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_editable:
            messages.warning(
                request,
                _("Lesson %(code)s is %(status)s and can no longer be edited.")
                % {
                    "code": self.object.lesson_code,
                    "status": self.object.get_status_display().lower(),
                },
            )
            return redirect("lessons:detail", pk=self.object.pk)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_editable:
            messages.error(
                request, _("A completed or cancelled lesson can no longer be edited.")
            )
            return redirect("lessons:detail", pk=self.object.pk)
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit lesson")
        context["warnings"] = getattr(context.get("form"), "warnings", [])
        return context

    def get_success_url(self):
        return reverse("lessons:detail", kwargs={"pk": self.object.pk})


class LessonConflictCheckView(CapabilityRequiredMixin, View):
    """HTMX endpoint that answers "can this lesson run?" while the form is open.

    Guarded by ``lessons.change`` because every role that may schedule a lesson
    also holds it, and it must be reachable from the edit screen too.
    """

    capability = "lessons.change"

    def post(self, request, *args, **kwargs):
        form = LessonConflictCheckForm(request.POST)
        conflicts: list[str] = []
        warnings: list[str] = []
        suggested = None
        if form.is_valid():
            proposal = form.as_proposal()
            exclude_pk = form.cleaned_data.get("lesson_id")
            conflicts = check_lesson_conflicts(proposal, exclude_pk=exclude_pk)
            warnings = check_lesson_warnings(proposal, exclude_pk=exclude_pk)
            if proposal["lesson_type"] is not None:
                suggested = suggest_capacity(
                    proposal["lesson_type"],
                    instructor_count=1 + len(proposal["assistant_instructors"]),
                    has_minors=False,
                )
        else:
            conflicts = [
                str(message) for messages_ in form.errors.values() for message in messages_
            ]
        return render(
            request,
            "lessons/partials/conflicts.html",
            {
                "conflicts": conflicts,
                "warnings": warnings,
                "suggested_capacity": suggested,
                "checked": True,
            },
        )


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------
class LessonCancelView(CapabilityRequiredMixin, View):
    """Confirm on GET, cancel on POST."""

    capability = "lessons.change"

    def get_lesson(self) -> Lesson:
        return get_object_or_404(
            Lesson.objects.select_related("lesson_type", "spot", "instructor"),
            pk=self.kwargs["pk"],
        )

    def get(self, request, *args, **kwargs):
        lesson = self.get_lesson()
        return render(
            request,
            "lessons/lesson_confirm_cancel.html",
            {
                "lesson": lesson,
                "form": LessonCancelForm(),
                "attendees": lesson.active_attendances().select_related("student"),
            },
        )

    def post(self, request, *args, **kwargs):
        lesson = self.get_lesson()
        form = LessonCancelForm(request.POST)
        if form.is_valid():
            try:
                cancel_lesson(
                    lesson, form.cleaned_data["reason"], user=request.user, request=request
                )
            except ValidationError as exc:
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                messages.success(
                    request,
                    _("Lesson %(code)s cancelled.") % {"code": lesson.lesson_code},
                )
                return redirect("lessons:detail", pk=lesson.pk)
        return render(
            request,
            "lessons/lesson_confirm_cancel.html",
            {
                "lesson": lesson,
                "form": form,
                "attendees": lesson.active_attendances().select_related("student"),
            },
        )


class LessonCompleteView(CapabilityRequiredMixin, View):
    capability = "lessons.change"

    def post(self, request, *args, **kwargs):
        lesson = get_object_or_404(Lesson, pk=kwargs["pk"])
        form = LessonCompleteForm(request.POST)
        form.is_valid()
        try:
            complete_lesson(
                lesson,
                mark_unchecked_as_no_show=bool(
                    form.cleaned_data.get("mark_unchecked_as_no_show")
                ),
                user=request.user,
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("Lesson %(code)s completed.") % {"code": lesson.lesson_code},
            )
        return redirect("lessons:detail", pk=lesson.pk)


class LessonSafetyCheckView(CapabilityRequiredMixin, View):
    """Record the named staff sign-off on the safety briefing."""

    capability = "lessons.change"

    def post(self, request, *args, **kwargs):
        lesson = get_object_or_404(Lesson, pk=kwargs["pk"])
        try:
            mark_safety_check(lesson, request.user, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Safety briefing recorded."))
        return redirect("lessons:detail", pk=lesson.pk)


class LessonConditionsView(CapabilityRequiredMixin, View):
    """Freeze the surf conditions onto the lesson record."""

    capability = "lessons.change"

    def post(self, request, *args, **kwargs):
        lesson = get_object_or_404(Lesson.objects.select_related("spot"), pk=kwargs["pk"])
        snapshot = capture_conditions_snapshot(lesson, user=request.user, request=request)
        if snapshot:
            messages.success(request, _("Surf conditions captured."))
        else:
            messages.warning(
                request, _("No surf condition reading is available for this spot yet.")
            )
        return redirect("lessons:detail", pk=lesson.pk)


# ---------------------------------------------------------------------------
# Roster actions (HTMX)
# ---------------------------------------------------------------------------
class RosterActionView(CapabilityRequiredMixin, View):
    """Base for the POST-only roster actions.

    Each subclass implements :meth:`perform` and gets consistent handling: a
    refreshed roster fragment for HTMX callers, a redirect with a flash message
    for everyone else, and service ``ValidationError``s surfaced either way.
    """

    capability = "lessons.change"
    success_message = _("Roster updated.")

    def get_lesson(self) -> Lesson:
        return get_object_or_404(
            Lesson.objects.select_related("lesson_type", "spot", "instructor"),
            pk=self.kwargs["pk"],
        )

    def perform(self, request, lesson: Lesson) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        lesson = self.get_lesson()
        error = ""
        try:
            self.perform(request, lesson)
        except ValidationError as exc:
            error = " ".join(str(message) for message in exc.messages)

        lesson.refresh_from_db()
        if getattr(request, "htmx", False):
            return render_roster(request, lesson, error=error)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, self.success_message)
        return redirect("lessons:detail", pk=lesson.pk)

    def get_attendance(self, lesson: Lesson) -> LessonAttendance:
        return get_object_or_404(
            LessonAttendance.objects.select_related("student", "lesson"),
            pk=self.request.POST.get("attendance") or self.kwargs.get("attendance_pk"),
            lesson=lesson,
        )


class AttendanceAddView(RosterActionView):
    capability = "lessons.change"
    success_message = _("Student added to the lesson.")

    def perform(self, request, lesson):
        form = AddAttendeeForm(request.POST, lesson=lesson)
        if not form.is_valid():
            raise ValidationError(
                [message for field in form.errors.values() for message in field]
            )
        add_student_to_lesson(
            lesson,
            form.cleaned_data["student"],
            booking=form.cleaned_data.get("booking"),
            user=request.user,
            request=request,
        )


class AttendanceRemoveView(RosterActionView):
    success_message = _("Student removed from the lesson.")

    def perform(self, request, lesson):
        attendance = self.get_attendance(lesson)
        remove_student_from_lesson(
            lesson,
            attendance.student,
            reason=request.POST.get("reason", ""),
            user=request.user,
            request=request,
        )


class AttendanceCheckInView(RosterActionView):
    success_message = _("Student checked in.")

    def perform(self, request, lesson):
        check_in_student(self.get_attendance(lesson), user=request.user, request=request)


class AttendanceNoShowView(RosterActionView):
    success_message = _("Student marked as a no-show.")

    def perform(self, request, lesson):
        mark_no_show(self.get_attendance(lesson), user=request.user, request=request)


class AttendanceEquipmentView(RosterActionView):
    success_message = _("Equipment assigned.")

    def perform(self, request, lesson):
        attendance = self.get_attendance(lesson)
        form = AssignEquipmentForm(request.POST, attendance=attendance)
        if not form.is_valid():
            raise ValidationError(
                [message for field in form.errors.values() for message in field]
            )
        assign_equipment_to_attendance(
            attendance,
            board=form.cleaned_data.get("board"),
            wetsuit=form.cleaned_data.get("wetsuit"),
            user=request.user,
            request=request,
        )


class AttendanceFeedbackView(RosterActionView):
    success_message = _("Feedback saved.")

    def perform(self, request, lesson):
        attendance = self.get_attendance(lesson)
        form = AttendanceFeedbackForm(request.POST, instance=attendance)
        if not form.is_valid():
            raise ValidationError(
                [message for field in form.errors.values() for message in field]
            )
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()


# ---------------------------------------------------------------------------
# Lesson catalogue
# ---------------------------------------------------------------------------
class LessonTypeListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    capability = "lessons.view"
    # The teaching catalogue is the same list every customer is quoted from.
    external_access = SHARED
    model = LessonType
    template_name = "lessons/lessontype_list.html"
    partial_template_name = "lessons/partials/lessontype_table.html"
    context_object_name = "lesson_types"
    paginate_by = 25
    search_fields = ("code", "name", "description")

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.GET.get("category", "").strip()
        if category:
            queryset = queryset.filter(category=category)
        state = self.request.GET.get("state", "").strip()
        if state == "active":
            queryset = queryset.filter(is_active=True)
        elif state == "archived":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = LessonType.Category.choices
        context["current_category"] = self.request.GET.get("category", "")
        context["current_state"] = self.request.GET.get("state", "")
        return context


class LessonTypeCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "lessons.manage"
    model = LessonType
    form_class = LessonTypeForm
    template_name = "lessons/lessontype_form.html"
    success_url = reverse_lazy("lessons:type_list")
    success_message = _("Lesson type created.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New lesson type")
        return context


class LessonTypeUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "lessons.manage"
    model = LessonType
    form_class = LessonTypeForm
    template_name = "lessons/lessontype_form.html"
    context_object_name = "lesson_type"
    success_url = reverse_lazy("lessons:type_list")
    success_message = _("Lesson type updated.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit lesson type")
        return context
