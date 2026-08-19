"""HTML views for the Training Center.

``training.view`` is one of the base capabilities, so every staff member and
every student can open these screens. Courses that need a capability the reader
does not hold are filtered out in :func:`apps.training.selectors.courses_for`
rather than shown and then refused.

Progress is per user and is only ever written for ``request.user`` — there is no
route through which one person can mark another person's training complete.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin

from . import selectors, services
from .models import TrainingCourse, TrainingProgress, TrainingStep


class TrainingHomeView(CapabilityRequiredMixin, TemplateView):
    """Course grid with a progress ring on every card."""

    capability = "training.view"
    template_name = "training/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        summaries = services.course_progress(self.request.user)

        context["summaries"] = summaries
        context["overall"] = services.overall_progress(self.request.user)
        context["in_progress"] = [s for s in summaries if s.is_started and not s.is_completed]
        return context


class TrainingCourseDetailView(CapabilityRequiredMixin, DetailView):
    """Lesson outline with per-lesson completion."""

    capability = "training.view"
    model = TrainingCourse
    template_name = "training/course_detail.html"
    context_object_name = "course"

    def get_queryset(self):
        return selectors.courses_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course: TrainingCourse = self.object
        progress = selectors.progress_for(self.request.user, course)

        context["summary"] = services.course_progress(self.request.user, course)
        context["progress"] = progress
        context["completed_ids"] = progress.completed_step_ids if progress else set()
        context["lessons"] = [
            services.lesson_progress(lesson, progress)
            for lesson in selectors.lessons_for(course).prefetch_related("steps")
        ]
        return context


class TrainingStepView(CapabilityRequiredMixin, DetailView):
    """One step: the instruction, a link to the real screen, previous / next."""

    capability = "training.view"
    model = TrainingStep
    template_name = "training/step_detail.html"
    context_object_name = "step"

    def get_queryset(self):
        # Reuse the capability filter: a step of a hidden course is a 404.
        visible_courses = selectors.courses_for(self.request.user)
        return selectors.step_detail_queryset().filter(lesson__course__in=visible_courses)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        step: TrainingStep = self.object
        course = step.lesson.course

        progress = services.start_course(self.request.user, course)
        previous_step, next_step = services.adjacent_steps(step)
        sequence = services.course_step_sequence(course)
        position = next(
            (index for index, item in enumerate(sequence) if item.pk == step.pk), 0
        ) + 1

        context["course"] = course
        context["lesson"] = step.lesson
        context["progress"] = progress
        context["summary"] = services.course_progress(self.request.user, course)
        context["previous_step"] = previous_step
        context["next_step"] = next_step
        context["step_position"] = position
        context["step_total"] = len(sequence)
        context["is_completed"] = step.pk in progress.completed_step_ids
        context["target_link"] = step.target_link
        return context


class StepCompleteView(CapabilityRequiredMixin, View):
    """POST-only: tick a step off (or untick it), for the signed-in user only."""

    capability = "training.view"
    template_name = "training/partials/step_status.html"

    def post(self, request, pk: int, *args, **kwargs):
        visible_courses = selectors.courses_for(request.user)
        step = get_object_or_404(
            selectors.step_detail_queryset().filter(lesson__course__in=visible_courses), pk=pk
        )

        undo = (request.POST.get("undo") or "").strip().lower() in {"1", "true", "yes"}
        if undo:
            progress = services.uncomplete_step(request.user, step)
        else:
            progress = services.complete_step(request.user, step)

        course = step.lesson.course
        _previous, next_step = services.adjacent_steps(step)
        context = {
            "step": step,
            "course": course,
            "progress": progress,
            "summary": services.course_progress(request.user, course),
            "next_step": next_step,
            "is_completed": step.pk in progress.completed_step_ids,
        }

        if not getattr(request, "htmx", False):
            # Without JavaScript the same button still works: post, then land on
            # the next step, or on the course when the last one is done.
            if undo:
                return redirect("training:step", pk=step.pk)
            if next_step is not None:
                return redirect("training:step", pk=next_step.pk)
            messages.success(
                request,
                _("Course completed: %(title)s") % {"title": course.title},
            )
            return redirect("training:course", pk=course.pk)

        return render(request, self.template_name, context)


class CourseResetView(CapabilityRequiredMixin, View):
    """POST-only: clear the signed-in user's progress on one course."""

    capability = "training.view"

    def post(self, request, pk: int, *args, **kwargs):
        course = get_object_or_404(selectors.courses_for(request.user), pk=pk)
        services.reset_course(request.user, course)
        messages.success(
            request, _("Progress on “%(title)s” has been reset.") % {"title": course.title}
        )
        return redirect("training:course", pk=course.pk)


class CourseStartView(CapabilityRequiredMixin, View):
    """POST-only: start or resume a course and jump to the right step."""

    capability = "training.view"

    def post(self, request, pk: int, *args, **kwargs):
        course = get_object_or_404(selectors.courses_for(request.user), pk=pk)
        progress = services.start_course(request.user, course)
        target = services.first_incomplete_step(course, progress)
        if target is None:
            messages.info(
                request,
                _("“%(title)s” has no steps yet.") % {"title": course.title},
            )
            return redirect("training:course", pk=course.pk)
        return redirect("training:step", pk=target.pk)


class MyProgressView(CapabilityRequiredMixin, TemplateView):
    """Everything the signed-in user has and has not worked through."""

    capability = "training.view"
    template_name = "training/progress.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        overall = services.overall_progress(self.request.user)

        context["overall"] = overall
        context["summaries"] = overall["summaries"]
        context["completed"] = [s for s in overall["summaries"] if s.is_completed]
        context["in_progress"] = [
            s for s in overall["summaries"] if s.is_started and not s.is_completed
        ]
        context["not_started"] = [s for s in overall["summaries"] if not s.is_started]
        context["status_choices"] = TrainingProgress.Status
        context["home_url"] = reverse("training:home")
        return context
