"""Student HTML views."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.enums import SurfLevel
from apps.core.mixins import AuditedCreateMixin, AuditedUpdateMixin, HtmxPartialMixin
from apps.customers.models import Customer

from . import selectors, services
from .forms import (
    SkillAssessmentForm,
    StudentFilterForm,
    StudentForm,
    StudentWithCustomerForm,
)
from .models import SKILL_FIELDS, SkillAssessment, Student

#: SVG geometry for the progress chart (viewBox 0 0 640 200).
CHART_LEFT, CHART_RIGHT = 40.0, 620.0
CHART_TOP, CHART_BOTTOM = 20.0, 180.0


def progress_chart(series: list[dict]) -> dict:
    """Turn an assessment series into SVG coordinates.

    Pure geometry — the numbers themselves come from
    :func:`apps.students.services.student_progress_series`.
    """
    grid = [
        {
            "score": score,
            "y": round(CHART_BOTTOM - (score - 1) / 4 * (CHART_BOTTOM - CHART_TOP), 1),
            "label_y": round(
                CHART_BOTTOM - (score - 1) / 4 * (CHART_BOTTOM - CHART_TOP) + 3, 1
            ),
        }
        for score in range(1, 6)
    ]
    points = []
    count = len(series)
    span = CHART_RIGHT - CHART_LEFT
    for index, item in enumerate(series):
        x = CHART_LEFT if count < 2 else CHART_LEFT + (index / (count - 1)) * span
        average = float(item.get("average") or 0)
        clamped = min(max(average, 1.0), 5.0)
        y = CHART_BOTTOM - (clamped - 1) / 4 * (CHART_BOTTOM - CHART_TOP)
        points.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "average": item.get("average"),
                "label": f"{item.get('date')} — {item.get('average')} / 5 · {item.get('level_label')}",
            }
        )
    return {
        "grid": grid,
        "points": points,
        "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        "first_date": series[0]["date"] if series else "",
        "last_date": series[-1]["date"] if series else "",
    }


#: Badge palette for the six surf levels, darkest at the top of the ladder.
LEVEL_COLORS = {
    SurfLevel.FIRST_TIME: "slate",
    SurfLevel.BEGINNER: "sky",
    SurfLevel.ADVANCED_BEGINNER: "sky",
    SurfLevel.INTERMEDIATE: "emerald",
    SurfLevel.ADVANCED: "violet",
    SurfLevel.COMPETITION: "amber",
}


class StudentListView(CapabilityRequiredMixin, HtmxPartialMixin, ListView):
    capability = "students.view"
    model = Student
    template_name = "students/student_list.html"
    partial_template_name = "students/partials/student_table.html"
    context_object_name = "students"
    paginate_by = 25

    def get_filter_form(self) -> StudentFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = StudentFilterForm(self.request.GET or None)
            self._filter_form.is_valid()
        return self._filter_form

    def get_queryset(self):
        form = self.get_filter_form()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        return selectors.student_list(
            search=data.get("q", ""),
            level=data.get("level", ""),
            instructor=data.get("instructor", ""),
            status=data.get("status", ""),
            needs_assessment=bool(data.get("needs_assessment")),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        context["search_term"] = self.request.GET.get("q", "")
        context["level_colors"] = LEVEL_COLORS
        context["total_count"] = Student.objects.count()
        context["active_count"] = Student.objects.active().count()
        return context


class StudentDetailView(CapabilityRequiredMixin, DetailView):
    capability = "students.view"
    model = Student
    template_name = "students/student_detail.html"
    context_object_name = "student"

    def get_queryset(self):
        return Student.objects.select_related("customer", "preferred_instructor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.object
        assessments = selectors.assessments_for(student)
        context["assessments"] = assessments
        context["latest_assessment"] = assessments[0] if assessments else None
        series = services.student_progress_series(student)
        context["progress_series"] = series
        context["chart"] = progress_chart(series)
        context["skill_fields"] = SKILL_FIELDS
        context["level_colors"] = LEVEL_COLORS
        context["has_valid_waiver"] = student.customer.has_valid_waiver()
        context["restrictions"] = selectors.student_restrictions(student)
        return context


class StudentLessonHistoryView(CapabilityRequiredMixin, View):
    """HTMX partial: lesson history, loaded after the profile paints."""

    capability = "students.view"

    def get(self, request, pk: int):
        student = get_object_or_404(Student.objects.select_related("customer"), pk=pk)
        return render(
            request,
            "students/partials/lesson_history.html",
            {
                "student": student,
                "rows": selectors.lesson_history(student),
                "upcoming": selectors.upcoming_lessons(student),
            },
        )


class StudentCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    """Attach a student profile to an existing customer."""

    capability = "students.add"
    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    success_message = _("Student profile created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        customer_id = self.request.GET.get("customer")
        if customer_id and str(customer_id).isdigit():
            customer = Customer.objects.filter(
                pk=customer_id, student_profile__isnull=True
            ).first()
            if customer is not None:
                initial["customer"] = customer.pk
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New student")
        context["submit_label"] = _("Create student")
        return context

    def get_success_url(self):
        return reverse("students:detail", kwargs={"pk": self.object.pk})


class StudentRegisterView(CapabilityRequiredMixin, TemplateView):
    """One screen that creates the customer and the student together."""

    capability = "students.add"
    template_name = "students/student_register.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", StudentWithCustomerForm())
        context["title"] = _("Register a new student")
        return context

    def post(self, request, *args, **kwargs):
        form = StudentWithCustomerForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                student = services.create_student_with_customer(
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    actor=request.user,
                    request=request,
                    allow_duplicate=data["allow_duplicate"],
                    customer_fields={
                        "email": data["email"],
                        "phone": data["phone"],
                        "birth_date": data["birth_date"],
                        "emergency_contact_name": data["emergency_contact_name"],
                        "emergency_contact_phone": data["emergency_contact_phone"],
                        "emergency_contact_relation": data["emergency_contact_relation"],
                    },
                    surf_level=data["surf_level"],
                    can_swim=data["can_swim"],
                    swim_distance_m=data["swim_distance_m"],
                    goals=data["goals"],
                    medical_conditions=data["medical_conditions"],
                )
            except ValidationError as exc:
                for field, errors in getattr(exc, "message_dict", {"__all__": exc.messages}).items():
                    target = field if field in form.fields else None
                    for error in errors:
                        form.add_error(target, error)
                return self.render_to_response(self.get_context_data(form=form))

            messages.success(
                request,
                _("%(name)s was registered as %(code)s.")
                % {"name": student.full_name, "code": student.student_code},
            )
            return redirect("students:detail", pk=student.pk)
        return self.render_to_response(self.get_context_data(form=form))


class StudentUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "students.change"
    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    context_object_name = "student"
    success_message = _("Student updated.")

    def get_queryset(self):
        return Student.objects.select_related("customer")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit student")
        context["submit_label"] = _("Save changes")
        return context

    def get_success_url(self):
        return reverse("students:detail", kwargs={"pk": self.object.pk})


class StudentToggleActiveView(CapabilityRequiredMixin, View):
    capability = "students.change"

    def post(self, request, pk: int):
        student = get_object_or_404(Student.objects.all(), pk=pk)
        services.set_active(student, not student.is_active, actor=request.user, request=request)
        messages.success(
            request,
            _("Student reactivated.") if student.is_active else _("Student archived."),
        )
        return redirect("students:detail", pk=student.pk)


class SkillAssessmentCreateView(CapabilityRequiredMixin, TemplateView):
    """Record an assessment. This is the only route that changes a level."""

    capability = "students.change"
    template_name = "students/assessment_form.html"

    def get_student(self) -> Student:
        return get_object_or_404(
            Student.objects.select_related("customer"), pk=self.kwargs["pk"]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student()
        context["student"] = student
        context.setdefault("form", SkillAssessmentForm(student=student))
        context["previous"] = student.latest_assessment
        context["skill_fields"] = SKILL_FIELDS
        return context

    def post(self, request, *args, **kwargs):
        student = self.get_student()
        form = SkillAssessmentForm(request.POST, student=student)
        if form.is_valid():
            data = form.cleaned_data
            try:
                assessment = services.record_assessment(
                    student,
                    paddling=data["paddling"],
                    popup=data["popup"],
                    positioning=data["positioning"],
                    wave_reading=data["wave_reading"],
                    safety=data["safety"],
                    instructor=data.get("instructor"),
                    assessed_on=data.get("assessed_on"),
                    level_after=data.get("level_after"),
                    notes=data.get("notes", ""),
                    next_focus=data.get("next_focus", ""),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                for field, errors in getattr(exc, "message_dict", {"__all__": exc.messages}).items():
                    target = field if field in form.fields else None
                    for error in errors:
                        form.add_error(target, error)
                return self.render_to_response(self.get_context_data(form=form))

            if assessment.level_changed:
                messages.success(
                    request,
                    _("Assessment saved. %(name)s is now %(level)s.")
                    % {
                        "name": student.full_name,
                        "level": student.get_surf_level_display(),
                    },
                )
            else:
                messages.success(request, _("Assessment saved."))
            return redirect("students:detail", pk=student.pk)
        return self.render_to_response(self.get_context_data(form=form))


class AssessmentListView(CapabilityRequiredMixin, HtmxPartialMixin, ListView):
    """All assessments across students — the head coach's review queue."""

    capability = "students.view"
    model = SkillAssessment
    template_name = "students/assessment_list.html"
    partial_template_name = "students/partials/assessment_table.html"
    context_object_name = "assessments"
    paginate_by = 25

    def get_queryset(self):
        queryset = SkillAssessment.objects.select_related(
            "student", "student__customer", "instructor"
        )
        instructor = self.request.GET.get("instructor", "")
        if instructor.isdigit():
            queryset = queryset.filter(instructor_id=instructor)
        level = self.request.GET.get("level", "")
        if level:
            queryset = queryset.filter(level_after=level)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["levels"] = SurfLevel.choices
        context["instructors"] = selectors.instructor_choices()
        context["current_level"] = self.request.GET.get("level", "")
        context["current_instructor"] = self.request.GET.get("instructor", "")
        return context
