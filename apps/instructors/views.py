"""HTML views for the instructor module.

Views orchestrate only: they read the request, ask :mod:`services` for the
decision, and render. Every screen declares the capability it requires, and
every state change goes through POST.
"""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.csv_safety import safe_csv_writer
from apps.core.enums import SurfLevel
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedDeleteMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from . import selectors, services
from .forms import (
    AvailabilitySearchForm,
    AvailabilitySlotForm,
    CertificationForm,
    InstructorForm,
    PerformanceReviewForm,
    TimeOffForm,
)
from .models import (
    EXPIRY_WARNING_DAYS,
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)

#: Badge palettes handed to ``{% status_badge %}``, which cannot build a dict.
CERTIFICATION_STATUS_COLORS = {
    "current": "emerald",
    "expiring": "amber",
    "expired": "rose",
    "unverified": "slate",
}
TIME_OFF_STATUS_COLORS = {"approved": "emerald", "pending": "amber"}
LEVEL_COLORS = {
    SurfLevel.FIRST_TIME: "slate",
    SurfLevel.BEGINNER: "sky",
    SurfLevel.ADVANCED_BEGINNER: "sky",
    SurfLevel.INTERMEDIATE: "violet",
    SurfLevel.ADVANCED: "emerald",
    SurfLevel.COMPETITION: "amber",
}

PERFORMANCE_WINDOW_DAYS = 90


def _safe_next(request, fallback: str) -> str:
    """Return the posted ``next`` URL only when it points back at this site."""
    candidate = request.POST.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


# ---------------------------------------------------------------------------
# Instructor list & profile
# ---------------------------------------------------------------------------
class InstructorListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    """Card grid of the coaching team with availability and paperwork state."""

    capability = "instructors.view"
    model = Instructor
    template_name = "instructors/instructor_list.html"
    partial_template_name = "instructors/partials/instructor_cards.html"
    context_object_name = "instructors"
    paginate_by = 24
    search_fields = (
        "instructor_code",
        "user__first_name",
        "user__last_name",
        "user__email",
    )
    queryset = selectors.instructor_queryset()

    def get_queryset(self):
        queryset = selectors.annotate_certification_counts(super().get_queryset())

        status = self.request.GET.get("status", "").strip()
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)
        elif status == "bookable":
            queryset = queryset.filter(is_active=True, is_available_for_booking=True)

        level = self.request.GET.get("level", "").strip()
        if level in dict(SurfLevel.choices):
            queryset = queryset.filter(max_level_taught=level)

        if self.request.GET.get("certs", "").strip() == "warning":
            queryset = queryset.filter(Q(expiring_count__gt=0) | Q(expired_count__gt=0))
        # Annotation introduces a GROUP BY, which makes Django forget the Meta
        # ordering for pagination purposes — so state it explicitly.
        return queryset.order_by("user__first_name", "user__last_name", "instructor_code")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = Instructor.objects.all()
        context.update(
            {
                "levels": SurfLevel.choices,
                "current_status": self.request.GET.get("status", ""),
                "current_level": self.request.GET.get("level", ""),
                "current_certs": self.request.GET.get("certs", ""),
                "total_count": base.count(),
                "active_count": base.filter(is_active=True).count(),
                "bookable_count": base.filter(
                    is_active=True, is_available_for_booking=True
                ).count(),
                "attention_count": len(selectors.instructors_needing_attention(limit=500)),
                "level_colors": LEVEL_COLORS,
                "expiry_warning_days": EXPIRY_WARNING_DAYS,
            }
        )
        return context


class InstructorDetailView(CapabilityRequiredMixin, DetailView):
    """Everything an operations manager needs about one coach on one screen."""

    capability = "instructors.view"
    model = Instructor
    template_name = "instructors/instructor_detail.html"
    context_object_name = "instructor"
    queryset = Instructor.objects.select_related("user")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        instructor = self.object
        today = timezone.localdate()
        period_start = today - dt.timedelta(days=PERFORMANCE_WINDOW_DAYS - 1)

        context.update(
            {
                "certifications": selectors.certifications_for(instructor),
                "certification_summary": selectors.certification_summary(instructor),
                "week": services.weekly_availability(instructor, today),
                "upcoming_lessons": selectors.upcoming_lessons(instructor),
                "performance": services.instructor_performance(
                    instructor, period_start, today
                ),
                "performance_days": PERFORMANCE_WINDOW_DAYS,
                "time_off_periods": instructor.time_off_periods.select_related(
                    "approved_by"
                ).order_by("-start_date")[:10],
                "reviews": selectors.recent_reviews(instructor),
                "blockers": services.assignment_blockers(instructor),
                "cert_status_colors": CERTIFICATION_STATUS_COLORS,
                "time_off_colors": TIME_OFF_STATUS_COLORS,
                "level_colors": LEVEL_COLORS,
                "expiry_warning_days": EXPIRY_WARNING_DAYS,
            }
        )
        return context


class InstructorCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "instructors.add"
    model = Instructor
    form_class = InstructorForm
    template_name = "instructors/instructor_form.html"
    success_message = _("Instructor profile created.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New instructor")
        context["cancel_url"] = reverse("instructors:list")
        return context

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.object.pk})


class InstructorUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "instructors.change"
    model = Instructor
    form_class = InstructorForm
    template_name = "instructors/instructor_form.html"
    context_object_name = "instructor"
    success_message = _("Instructor profile updated.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit instructor")
        context["cancel_url"] = reverse("instructors:detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.object.pk})


class InstructorDeleteView(CapabilityRequiredMixin, AuditedDeleteMixin, DeleteView):
    """Soft-delete a profile, but never while future lessons depend on it."""

    capability = "instructors.delete"
    model = Instructor
    template_name = "instructors/instructor_confirm_delete.html"
    context_object_name = "instructor"
    success_url = reverse_lazy("instructors:list")
    success_message = _("Instructor profile removed.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed, reason = services.can_delete_instructor(self.object)
        context["deletion_allowed"] = allowed
        context["deletion_reason"] = reason
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        allowed, reason = services.can_delete_instructor(self.object)
        if not allowed:
            messages.error(request, reason)
            return redirect("instructors:detail", pk=self.object.pk)
        return super().post(request, *args, **kwargs)


class InstructorBookingToggleView(CapabilityRequiredMixin, View):
    """Take an instructor in or out of the booking pool (POST only)."""

    capability = "instructors.change"

    def post(self, request, pk: int, *args, **kwargs):
        instructor = get_object_or_404(Instructor, pk=pk)
        wanted = request.POST.get("available") == "1"
        services.set_booking_availability(instructor, wanted, request=request)
        messages.success(
            request,
            _("%(name)s is now open for bookings.") % {"name": instructor.full_name}
            if wanted
            else _("%(name)s has been withdrawn from the booking pool.")
            % {"name": instructor.full_name},
        )
        return redirect("instructors:detail", pk=instructor.pk)


class InstructorExportView(CapabilityRequiredMixin, View):
    """CSV of the coaching team. Pay data is hidden without the extra capability."""

    capability = "instructors.export"

    def get(self, request, *args, **kwargs):
        show_pay = request.user.has_capability("instructors.view_commission")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename = f"instructors-{timezone.localdate():%Y%m%d}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM so Excel opens UTF-8 correctly

        header = [
            str(_("Code")),
            str(_("Name")),
            str(_("E-mail")),
            str(_("Highest level")),
            str(_("Max students")),
            str(_("Active")),
            str(_("Open for bookings")),
            str(_("Rating")),
            str(_("Lessons taught")),
            str(_("Certifications current")),
        ]
        if show_pay:
            header += [str(_("Hourly rate")), str(_("Commission %"))]

        writer = safe_csv_writer(response, delimiter=";")
        writer.writerow(header)
        for instructor in selectors.instructor_queryset():
            row = [
                instructor.instructor_code,
                instructor.full_name,
                instructor.user.email if instructor.user_id else "",
                str(SurfLevel(instructor.max_level_taught).label),
                instructor.max_students_per_lesson,
                str(_("Yes")) if instructor.is_active else str(_("No")),
                str(_("Yes")) if instructor.is_available_for_booking else str(_("No")),
                f"{instructor.rating_average:.2f}",
                instructor.total_lessons_taught,
                str(_("Yes")) if instructor.has_valid_certifications else str(_("No")),
            ]
            if show_pay:
                row += [f"{instructor.hourly_rate:.2f}", f"{instructor.commission_percent:.2f}"]
            writer.writerow(row)

        record_audit(
            request,
            action=AuditAction.EXPORT,
            description=_("Instructor list exported to CSV"),
        )
        return response


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------
class CertificationCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "instructors.change"
    model = Certification
    form_class = CertificationForm
    template_name = "instructors/certification_form.html"
    success_message = _("Certification recorded. It still needs verification.")

    def dispatch(self, request, *args, **kwargs):
        self.instructor = get_object_or_404(Instructor, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instructor"] = self.instructor
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["instructor"] = self.instructor
        context["title"] = _("New certification")
        context["cancel_url"] = reverse(
            "instructors:detail", kwargs={"pk": self.instructor.pk}
        )
        return context

    def form_valid(self, form):
        form.instance.instructor = self.instructor
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.instructor.pk})


class CertificationUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "instructors.change"
    model = Certification
    form_class = CertificationForm
    template_name = "instructors/certification_form.html"
    context_object_name = "certification"
    success_message = _("Certification updated.")
    queryset = Certification.objects.select_related("instructor", "instructor__user")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instructor"] = self.object.instructor
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["instructor"] = self.object.instructor
        context["title"] = _("Edit certification")
        context["cancel_url"] = reverse(
            "instructors:detail", kwargs={"pk": self.object.instructor_id}
        )
        return context

    def form_valid(self, form):
        # Any change to the evidence invalidates the previous verification.
        watched = {"kind", "name", "certificate_number", "issued_on", "expires_on", "document"}
        if watched & set(form.changed_data):
            form.instance.is_verified = False
            form.instance.verified_by = None
            form.instance.verified_at = None
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.object.instructor_id})


class CertificationVerifyView(CapabilityRequiredMixin, View):
    """A named staff member confirms they have seen the original certificate."""

    capability = "instructors.approve"

    def post(self, request, pk: int, *args, **kwargs):
        certification = get_object_or_404(
            Certification.objects.select_related("instructor"), pk=pk
        )
        if certification.is_expired:
            messages.error(
                request,
                _("%(name)s has already expired and cannot be verified.")
                % {"name": certification.name},
            )
        else:
            services.verify_certification(certification, request.user, request=request)
            messages.success(request, _("Certification verified."))
        return redirect("instructors:detail", pk=certification.instructor_id)


class CertificationDeleteView(CapabilityRequiredMixin, View):
    capability = "instructors.delete"

    def post(self, request, pk: int, *args, **kwargs):
        certification = get_object_or_404(
            Certification.objects.select_related("instructor"), pk=pk
        )
        instructor_id = certification.instructor_id
        record_audit(
            request,
            action=AuditAction.DELETE,
            instance=certification,
            description=_("Certification %(name)s removed") % {"name": certification.name},
        )
        certification.delete()
        messages.success(request, _("Certification removed."))
        return redirect("instructors:detail", pk=instructor_id)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def _availability_context(instructor: Instructor, form: AvailabilitySlotForm | None = None) -> dict:
    return {
        "instructor": instructor,
        "week": services.weekly_availability(instructor),
        "form": form or AvailabilitySlotForm(),
        "weekdays": AvailabilitySlot.Weekday.choices,
    }


def _render_grid(request, instructor: Instructor, form: AvailabilitySlotForm | None = None):
    return render(
        request,
        "instructors/partials/availability_grid.html",
        _availability_context(instructor, form),
    )


class AvailabilityEditorView(CapabilityRequiredMixin, TemplateView):
    """Weekly grid editor. The grid itself is swapped in place over HTMX."""

    capability = "instructors.change"
    template_name = "instructors/availability_editor.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        instructor = get_object_or_404(Instructor, pk=self.kwargs["pk"])
        context.update(_availability_context(instructor))
        return context


class AvailabilitySlotCreateView(CapabilityRequiredMixin, View):
    capability = "instructors.change"

    def post(self, request, pk: int, *args, **kwargs):
        instructor = get_object_or_404(Instructor, pk=pk)
        form = AvailabilitySlotForm(request.POST)
        if form.is_valid():
            try:
                services.create_availability_slot(
                    instructor,
                    weekday=form.cleaned_data["weekday"],
                    start_time=form.cleaned_data["start_time"],
                    end_time=form.cleaned_data["end_time"],
                    valid_from=form.cleaned_data.get("valid_from"),
                    valid_until=form.cleaned_data.get("valid_until"),
                    is_active=form.cleaned_data.get("is_active", True),
                    request=request,
                )
            except ValidationError as error:
                for field, errors in error.message_dict.items():
                    target = field if field in form.fields else None
                    for message in errors:
                        form.add_error(target, message)
            else:
                if getattr(request, "htmx", False):
                    return _render_grid(request, instructor)
                messages.success(request, _("Availability added."))
                return redirect("instructors:availability_editor", pk=instructor.pk)

        if getattr(request, "htmx", False):
            return _render_grid(request, instructor, form)
        for errors in form.errors.values():
            for message in errors:
                messages.error(request, message)
        return redirect("instructors:availability_editor", pk=instructor.pk)


class AvailabilitySlotToggleView(CapabilityRequiredMixin, View):
    capability = "instructors.change"

    def post(self, request, pk: int, *args, **kwargs):
        slot = get_object_or_404(AvailabilitySlot.objects.select_related("instructor"), pk=pk)
        slot.is_active = not slot.is_active
        slot.save(update_fields=["is_active", "updated_at"])
        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=slot.instructor,
            description=_("Availability %(slot)s set to %(state)s")
            % {
                "slot": str(slot),
                "state": _("active") if slot.is_active else _("inactive"),
            },
        )
        if getattr(request, "htmx", False):
            return _render_grid(request, slot.instructor)
        return redirect("instructors:availability_editor", pk=slot.instructor_id)


class AvailabilitySlotDeleteView(CapabilityRequiredMixin, View):
    capability = "instructors.change"

    def post(self, request, pk: int, *args, **kwargs):
        slot = get_object_or_404(AvailabilitySlot.objects.select_related("instructor"), pk=pk)
        instructor = slot.instructor
        record_audit(
            request,
            action=AuditAction.DELETE,
            instance=instructor,
            description=_("Availability removed: %(slot)s") % {"slot": str(slot)},
        )
        slot.delete()
        if getattr(request, "htmx", False):
            return _render_grid(request, instructor)
        messages.success(request, _("Availability removed."))
        return redirect("instructors:availability_editor", pk=instructor.pk)


class AvailabilityBoardView(CapabilityRequiredMixin, TemplateView):
    """"Who is free?" — search the whole team for one window."""

    capability = "instructors.view"
    template_name = "instructors/availability_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        has_query = bool(self.request.GET.get("date"))
        form = AvailabilitySearchForm(self.request.GET or None)
        results = []
        searched = False
        if has_query and form.is_valid():
            searched = True
            queryset = services.available_instructors(
                form.cleaned_data["date"],
                form.cleaned_data["start_time"],
                form.cleaned_data["end_time"],
                level=form.cleaned_data.get("level") or None,
            )
            for instructor in queryset:
                results.append(
                    {
                        "instructor": instructor,
                        "blockers": services.assignment_blockers(
                            instructor, level=form.cleaned_data.get("level") or None
                        ),
                    }
                )
        context.update(
            {
                "form": form,
                "results": results,
                "searched": searched,
                "roster": selectors.roster_for_date(timezone.localdate()),
                "today": timezone.localdate(),
                "level_colors": LEVEL_COLORS,
            }
        )
        return context

    def get_template_names(self):
        if getattr(self.request, "htmx", False):
            return ["instructors/partials/availability_results.html"]
        return [self.template_name]


# ---------------------------------------------------------------------------
# Time off
# ---------------------------------------------------------------------------
class TimeOffListView(CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView):
    capability = "instructors.view"
    model = TimeOff
    template_name = "instructors/timeoff_list.html"
    partial_template_name = "instructors/partials/timeoff_table.html"
    context_object_name = "time_off_periods"
    paginate_by = 25
    search_fields = ("instructor__user__first_name", "instructor__user__last_name", "note")
    queryset = TimeOff.objects.select_related(
        "instructor", "instructor__user", "approved_by"
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        state = self.request.GET.get("state", "pending").strip()
        if state == "pending":
            queryset = queryset.filter(is_approved=False)
        elif state == "approved":
            queryset = queryset.filter(is_approved=True)
        elif state == "current":
            today = timezone.localdate()
            queryset = queryset.filter(
                is_approved=True, start_date__lte=today, end_date__gte=today
            )
        return queryset.order_by("-start_date")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context.update(
            {
                "current_state": self.request.GET.get("state", "pending"),
                "pending_count": TimeOff.objects.filter(is_approved=False).count(),
                "current_count": TimeOff.objects.filter(
                    is_approved=True, start_date__lte=today, end_date__gte=today
                ).count(),
                "time_off_colors": TIME_OFF_STATUS_COLORS,
                "today": today,
            }
        )
        return context


class TimeOffCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "instructors.add"
    model = TimeOff
    form_class = TimeOffForm
    template_name = "instructors/timeoff_form.html"
    success_message = _("Absence recorded. It takes effect once approved.")

    def dispatch(self, request, *args, **kwargs):
        self.instructor = get_object_or_404(Instructor, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instructor"] = self.instructor
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["instructor"] = self.instructor
        context["title"] = _("Request time off")
        context["cancel_url"] = reverse(
            "instructors:detail", kwargs={"pk": self.instructor.pk}
        )
        return context

    def form_valid(self, form):
        form.instance.instructor = self.instructor
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.instructor.pk})


class TimeOffApproveView(CapabilityRequiredMixin, View):
    capability = "instructors.approve"

    def post(self, request, pk: int, *args, **kwargs):
        time_off = get_object_or_404(
            TimeOff.objects.select_related("instructor", "instructor__user"), pk=pk
        )
        _period, affected = services.approve_time_off(time_off, request.user, request=request)
        messages.success(request, _("Time off approved."))
        if affected:
            messages.warning(
                request,
                _(
                    "%(count)s lesson(s) are already scheduled during this absence and "
                    "must be reassigned."
                )
                % {"count": affected},
            )
        return redirect(_safe_next(request, reverse("instructors:timeoff_list")))


class TimeOffCancelView(CapabilityRequiredMixin, View):
    """Withdraw an absence that has not finished yet."""

    capability = "instructors.delete"

    def post(self, request, pk: int, *args, **kwargs):
        time_off = get_object_or_404(TimeOff.objects.select_related("instructor"), pk=pk)
        if time_off.is_past:
            messages.error(request, _("Past absence is history and cannot be withdrawn."))
        else:
            record_audit(
                request,
                action=AuditAction.DELETE,
                instance=time_off,
                description=_("Time off withdrawn for %(name)s")
                % {"name": time_off.instructor.full_name},
            )
            time_off.delete()
            messages.success(request, _("Time off withdrawn."))
        return redirect(_safe_next(request, reverse("instructors:timeoff_list")))


# ---------------------------------------------------------------------------
# Performance reviews
# ---------------------------------------------------------------------------
class PerformanceReviewCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "instructors.change"
    model = PerformanceReview
    form_class = PerformanceReviewForm
    template_name = "instructors/performance_review_form.html"
    success_message = _("Performance review saved.")

    def dispatch(self, request, *args, **kwargs):
        self.instructor = get_object_or_404(Instructor, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        today = timezone.localdate()
        initial.setdefault("period_end", today)
        initial.setdefault("period_start", today - dt.timedelta(days=PERFORMANCE_WINDOW_DAYS))
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["instructor"] = self.instructor
        context["title"] = _("New performance review")
        context["cancel_url"] = reverse(
            "instructors:detail", kwargs={"pk": self.instructor.pk}
        )
        return context

    def form_valid(self, form):
        form.instance.instructor = self.instructor
        form.instance.reviewer = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("instructors:detail", kwargs={"pk": self.instructor.pk})
