"""HTML views for surf camps.

The camp screen is the operational hub of the module: programme, participants,
logistics and money live behind four tabs on one page so the desk never has to
hunt for the roster while a minibus is waiting.
"""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin, StaffOnlyMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.csv_safety import safe_csv_writer
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedDeleteMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from . import services
from .forms import (
    CampActivityForm,
    CampDayForm,
    CampFilterForm,
    CampParticipantForm,
    CancellationForm,
    SurfCampForm,
)
from .models import (
    CLOSED_CAMP_STATUSES,
    ActivityType,
    CampActivity,
    CampDay,
    CampParticipant,
    CampStatus,
    ParticipantStatus,
    SurfCamp,
)
from .selectors import camps_with_occupancy, participants_for

#: Badge palettes — passed to ``{% status_badge %}`` so camp colours are
#: consistent on every screen of the module.
CAMP_STATUS_COLORS = {
    CampStatus.DRAFT: "slate",
    CampStatus.PUBLISHED: "sky",
    CampStatus.FULL: "violet",
    CampStatus.RUNNING: "emerald",
    CampStatus.COMPLETED: "slate",
    CampStatus.CANCELLED: "rose",
}

PARTICIPANT_STATUS_COLORS = {
    ParticipantStatus.REGISTERED: "slate",
    ParticipantStatus.CONFIRMED: "sky",
    ParticipantStatus.ARRIVED: "emerald",
    ParticipantStatus.DEPARTED: "violet",
    ParticipantStatus.CANCELLED: "rose",
}

#: Only names present in ``static/vendor/icons`` are used here.
ACTIVITY_TYPE_ICONS = {
    ActivityType.SURF_LESSON: "waves",
    ActivityType.THEORY: "book-open",
    ActivityType.VIDEO_ANALYSIS: "play",
    ActivityType.YOGA: "sunrise",
    ActivityType.FITNESS: "activity",
    ActivityType.EXCURSION: "map-pin",
    ActivityType.MEAL: "clock",
    ActivityType.FREE_TIME: "umbrella",
    ActivityType.TRANSFER: "arrow-left-right",
    ActivityType.SOCIAL: "users",
    ActivityType.OTHER: "circle-check",
}


def _badge_context() -> dict:
    return {
        "camp_status_colors": CAMP_STATUS_COLORS,
        "participant_status_colors": PARTICIPANT_STATUS_COLORS,
        "activity_type_icons": ACTIVITY_TYPE_ICONS,
    }


def _parse_date(raw: str | None, fallback: date) -> date:
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


# ---------------------------------------------------------------------------
# Camp list
# ---------------------------------------------------------------------------
class SurfCampListView(
    CapabilityRequiredMixin, StaffOnlyMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    """Back-office camp index.

    The whole HTML camp module is an operational console: every screen in it
    reaches participants, dietary and medical flags, room allocations and
    money. Customers and students hold ``surf_camps.view`` so the self-service
    API can show them their own place in a camp; they do not get these
    screens.
    """

    capability = "surf_camps.view"
    model = SurfCamp
    template_name = "surf_camps/surfcamp_list.html"
    partial_template_name = "surf_camps/partials/camp_cards.html"
    context_object_name = "camps"
    paginate_by = 24
    search_fields = ("name", "code", "accommodation_name", "description")

    def get_queryset(self):
        queryset = camps_with_occupancy()
        today = timezone.localdate()

        status = self.request.GET.get("status", "").strip()
        if status in CampStatus.values:
            queryset = queryset.filter(status=status)

        period = (self.request.GET.get("period") or "upcoming").strip()
        if period == "upcoming":
            queryset = queryset.filter(end_date__gte=today).exclude(
                status__in=CLOSED_CAMP_STATUSES
            )
        elif period == "running":
            queryset = queryset.filter(start_date__lte=today, end_date__gte=today).exclude(
                status=CampStatus.CANCELLED
            )
        elif period == "past":
            queryset = queryset.filter(end_date__lt=today)

        if self.request.GET.get("archived") != "1":
            queryset = queryset.filter(is_active=True)

        order = "start_date" if period in {"upcoming", "running"} else "-start_date"
        return self.apply_search(queryset).order_by(order, "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_badge_context())
        context["filter_form"] = CampFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "status": self.request.GET.get("status", ""),
                "period": self.request.GET.get("period", "upcoming"),
            }
        )
        context["current_period"] = self.request.GET.get("period", "upcoming")
        context["current_status"] = self.request.GET.get("status", "")
        context["show_archived"] = self.request.GET.get("archived") == "1"
        return context


# ---------------------------------------------------------------------------
# Camp detail
# ---------------------------------------------------------------------------
class SurfCampDetailView(CapabilityRequiredMixin, StaffOnlyMixin, DetailView):
    # Renders the participant list, the dietary and medical flags and the
    # camp finances. Staff-only.
    capability = "surf_camps.view"
    model = SurfCamp
    template_name = "surf_camps/surfcamp_detail.html"
    context_object_name = "camp"

    def get_queryset(self):
        return SurfCamp.objects.select_related("spot", "lead_instructor").prefetch_related(
            "instructors",
            Prefetch(
                "days",
                queryset=CampDay.objects.select_related("spot").prefetch_related(
                    Prefetch(
                        "activities",
                        queryset=CampActivity.objects.select_related(
                            "instructor", "lesson"
                        ).order_by("start_time", "id"),
                    )
                ),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        camp = self.object
        roster_date = _parse_date(
            self.request.GET.get("date"),
            timezone.localdate() if camp.is_running else camp.start_date,
        )

        context.update(_badge_context())
        context["tab"] = self.request.GET.get("tab", "programme")
        context["alerts"] = services.camp_alerts(camp)
        context["finance"] = services.camp_financial_summary(camp)
        context["staffing"] = services.camp_staffing_summary(camp)
        context["participants"] = participants_for(camp)
        context["cancelled_participants"] = CampParticipant.objects.filter(
            camp=camp, status=ParticipantStatus.CANCELLED
        ).select_related("student")
        context["roster"] = services.camp_daily_roster(camp, roster_date)
        context["roster_date"] = roster_date
        context["cancellation_form"] = CancellationForm()
        context["transfer_participants"] = [
            participant
            for participant in context["participants"]
            if participant.needs_transfer
        ]
        context["dietary_participants"] = [
            participant
            for participant in context["participants"]
            if participant.dietary_requirements.strip()
        ]
        context["medical_participants"] = [
            participant for participant in context["participants"] if participant.has_medical_flag
        ]
        return context


# ---------------------------------------------------------------------------
# Camp create / update / delete
# ---------------------------------------------------------------------------
class SurfCampCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "surf_camps.add"
    model = SurfCamp
    form_class = SurfCampForm
    template_name = "surf_camps/surfcamp_form.html"
    success_message = _("Camp created. The daily programme is ready to fill in.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New surf camp")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # A camp without days cannot be scheduled, so build them immediately.
        services.create_camp_with_days(self.object)
        return response

    def get_success_url(self):
        return reverse("surf_camps:detail", kwargs={"pk": self.object.pk})


class SurfCampUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "surf_camps.change"
    model = SurfCamp
    form_class = SurfCampForm
    template_name = "surf_camps/surfcamp_form.html"

    def get_queryset(self):
        return SurfCamp.objects.select_related("spot")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit camp")
        context["camp"] = self.object
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # Dates may have moved: realign the day rows with the new range.
        services.create_camp_with_days(self.object)
        services.refresh_camp_status(self.object)
        return response

    def get_success_url(self):
        return reverse("surf_camps:detail", kwargs={"pk": self.object.pk})


class SurfCampDeleteView(CapabilityRequiredMixin, AuditedDeleteMixin, DeleteView):
    capability = "surf_camps.delete"
    model = SurfCamp
    template_name = "surf_camps/surfcamp_confirm_delete.html"
    context_object_name = "camp"
    success_url = reverse_lazy("surf_camps:list")
    success_message = _("Camp deleted.")

    def form_valid(self, form):
        camp = self.get_object()
        if camp.active_participants().exists():
            messages.error(
                self.request,
                _("This camp still has participants. Cancel the camp instead of deleting it."),
            )
            return redirect("surf_camps:detail", pk=camp.pk)
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Camp actions
# ---------------------------------------------------------------------------
class CampPublishView(CapabilityRequiredMixin, TemplateView):
    """POST-only: move a draft camp to published."""

    capability = "surf_camps.approve"

    def post(self, request, *args, **kwargs):
        camp = get_object_or_404(SurfCamp, pk=kwargs["pk"])
        try:
            services.publish_camp(camp, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request, _("Camp %(code)s is published.") % {"code": camp.code}
            )
        return redirect("surf_camps:detail", pk=camp.pk)


class CampCancelView(CapabilityRequiredMixin, TemplateView):
    """POST-only: cancel the camp and release every place."""

    capability = "surf_camps.approve"

    def post(self, request, *args, **kwargs):
        camp = get_object_or_404(SurfCamp, pk=kwargs["pk"])
        form = CancellationForm(request.POST)
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        try:
            services.cancel_camp(camp, reason=reason, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request, _("Camp %(code)s cancelled.") % {"code": camp.code}
            )
        return redirect("surf_camps:detail", pk=camp.pk)


class CampGenerateProgrammeView(CapabilityRequiredMixin, TemplateView):
    """POST-only: fill empty camp days with the default schedule."""

    capability = "surf_camps.change"

    def post(self, request, *args, **kwargs):
        camp = get_object_or_404(SurfCamp, pk=kwargs["pk"])
        replace = request.POST.get("replace") == "1"
        try:
            created = services.generate_default_programme(camp, replace=replace, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            if created:
                messages.success(
                    request,
                    _("%(count)s activities added to the programme.") % {"count": created},
                )
            else:
                messages.info(
                    request, _("Every day already has a programme — nothing was changed.")
                )
        return redirect(
            reverse("surf_camps:detail", kwargs={"pk": camp.pk}) + "?tab=programme"
        )


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
class ParticipantPanelMixin(CapabilityRequiredMixin):
    """Shared rendering for the HTMX-driven participants panel."""

    def get_camp(self) -> SurfCamp:
        return get_object_or_404(SurfCamp, pk=self.kwargs["pk"])

    def render_panel(self, request, camp: SurfCamp, form=None, error: str = "", status: int = 200):
        context = {
            "camp": camp,
            "participants": participants_for(camp),
            "cancelled_participants": CampParticipant.objects.filter(
                camp=camp, status=ParticipantStatus.CANCELLED
            ).select_related("student"),
            "participant_form": form,
            "panel_error": error,
            "staffing": services.camp_staffing_summary(camp),
            "finance": services.camp_financial_summary(camp),
            **_badge_context(),
        }
        if getattr(request, "htmx", False):
            return render(
                request, "surf_camps/partials/participant_panel.html", context, status=status
            )
        return render(request, "surf_camps/participant_page.html", context, status=status)


class ParticipantCreateView(ParticipantPanelMixin, TemplateView):
    capability = "surf_camps.change"
    template_name = "surf_camps/participant_page.html"

    def get(self, request, *args, **kwargs):
        camp = self.get_camp()
        return self.render_panel(request, camp, form=CampParticipantForm(camp=camp))

    def post(self, request, *args, **kwargs):
        camp = self.get_camp()
        form = CampParticipantForm(request.POST, camp=camp)
        if not form.is_valid():
            return self.render_panel(request, camp, form=form)

        data = dict(form.cleaned_data)
        student = data.pop("student")
        booking = data.pop("booking", None)
        try:
            participant = services.add_participant(
                camp, student, booking=booking, request=request, **data
            )
        except ValidationError as error:
            return self.render_panel(request, camp, form=form, error=" ".join(error.messages))

        camp.refresh_from_db()
        if getattr(request, "htmx", False):
            return self.render_panel(request, camp)
        messages.success(
            request,
            _("%(student)s added to the camp.") % {"student": participant.student},
        )
        return redirect(reverse("surf_camps:detail", kwargs={"pk": camp.pk}) + "?tab=participants")


class ParticipantUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "surf_camps.change"
    model = CampParticipant
    form_class = CampParticipantForm
    template_name = "surf_camps/participant_form.html"
    context_object_name = "participant"

    def get_queryset(self):
        return CampParticipant.objects.select_related("camp", "student", "booking")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["camp"] = self.object.camp
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.object.camp
        context["title"] = _("Edit participant")
        return context

    def get_success_url(self):
        return (
            reverse("surf_camps:detail", kwargs={"pk": self.object.camp_id})
            + "?tab=participants"
        )


class ParticipantRemoveView(ParticipantPanelMixin, TemplateView):
    """POST-only: cancel a place and free it."""

    capability = "surf_camps.change"

    def get_camp(self) -> SurfCamp:
        return self.participant.camp

    def post(self, request, *args, **kwargs):
        self.participant = get_object_or_404(
            CampParticipant.objects.select_related("camp", "student"), pk=kwargs["pk"]
        )
        camp = self.participant.camp
        reason = request.POST.get("reason", "").strip()
        try:
            services.remove_participant(self.participant, reason=reason, request=request)
        except ValidationError as error:
            if getattr(request, "htmx", False):
                return self.render_panel(request, camp, error=" ".join(error.messages))
            for message in error.messages:
                messages.error(request, message)
            return redirect(
                reverse("surf_camps:detail", kwargs={"pk": camp.pk}) + "?tab=participants"
            )

        camp.refresh_from_db()
        if getattr(request, "htmx", False):
            return self.render_panel(request, camp)
        messages.success(request, _("The place was cancelled and is free again."))
        return redirect(reverse("surf_camps:detail", kwargs={"pk": camp.pk}) + "?tab=participants")


class ParticipantStatusView(ParticipantPanelMixin, TemplateView):
    """POST-only: confirm, check in or check out a participant."""

    capability = "surf_camps.change"

    def get_camp(self) -> SurfCamp:
        return self.participant.camp

    def post(self, request, *args, **kwargs):
        self.participant = get_object_or_404(
            CampParticipant.objects.select_related("camp", "student"), pk=kwargs["pk"]
        )
        camp = self.participant.camp
        new_status = request.POST.get("status", "")
        error = ""
        try:
            services.set_participant_status(self.participant, new_status, request=request)
        except ValidationError as exception:
            error = " ".join(exception.messages)

        camp.refresh_from_db()
        if getattr(request, "htmx", False):
            return self.render_panel(request, camp, error=error)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, _("Participant updated."))
        return redirect(reverse("surf_camps:detail", kwargs={"pk": camp.pk}) + "?tab=participants")


class ParticipantPanelView(ParticipantPanelMixin, StaffOnlyMixin, TemplateView):
    """Plain re-render of the participants panel (HTMX refresh target)."""

    capability = "surf_camps.view"

    def get(self, request, *args, **kwargs):
        return self.render_panel(request, self.get_camp())


# ---------------------------------------------------------------------------
# Programme: days and activities
# ---------------------------------------------------------------------------
class CampDayCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "surf_camps.change"
    model = CampDay
    form_class = CampDayForm
    template_name = "surf_camps/campday_form.html"
    success_message = _("Camp day added.")

    def dispatch(self, request, *args, **kwargs):
        self.camp = get_object_or_404(SurfCamp, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["camp"] = self.camp
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        last = self.camp.days.order_by("-day_number").first()
        initial["day_number"] = (last.day_number + 1) if last else 1
        taken = set(self.camp.days.values_list("date", flat=True))
        free = [day for day in self.camp.date_list() if day not in taken]
        if free:
            initial["date"] = free[0]
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.camp
        context["title"] = _("New camp day")
        return context

    def form_valid(self, form):
        form.instance.camp = self.camp
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("surf_camps:detail", kwargs={"pk": self.camp.pk}) + "?tab=programme"


class CampDayUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "surf_camps.change"
    model = CampDay
    form_class = CampDayForm
    template_name = "surf_camps/campday_form.html"
    context_object_name = "day"

    def get_queryset(self):
        return CampDay.objects.select_related("camp", "spot")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["camp"] = self.object.camp
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.object.camp
        context["title"] = _("Edit camp day")
        return context

    def get_success_url(self):
        return reverse("surf_camps:detail", kwargs={"pk": self.object.camp_id}) + "?tab=programme"


class CampDayDeleteView(CapabilityRequiredMixin, AuditedDeleteMixin, DeleteView):
    capability = "surf_camps.change"
    model = CampDay
    template_name = "surf_camps/campday_confirm_delete.html"
    context_object_name = "day"
    success_message = _("Camp day removed.")

    def get_queryset(self):
        return CampDay.objects.select_related("camp")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.object.camp
        return context

    def get_success_url(self):
        return reverse("surf_camps:detail", kwargs={"pk": self.object.camp_id}) + "?tab=programme"


class CampActivityCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "surf_camps.change"
    model = CampActivity
    form_class = CampActivityForm
    template_name = "surf_camps/campactivity_form.html"
    success_message = _("Activity added to the programme.")

    def dispatch(self, request, *args, **kwargs):
        self.camp_day = get_object_or_404(
            CampDay.objects.select_related("camp", "spot"), pk=kwargs["pk"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["camp_day"] = self.camp_day
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.camp_day.camp
        context["day"] = self.camp_day
        context["title"] = _("New activity")
        return context

    def form_valid(self, form):
        form.instance.camp_day = self.camp_day
        if self.request.user.is_authenticated:
            form.instance.created_by = self.request.user
            form.instance.updated_by = self.request.user
        try:
            services.save_activity(form.instance, request=self.request)
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
            return self.form_invalid(form)
        self.object = form.instance
        record_audit(
            self.request,
            action=AuditAction.CREATE,
            instance=self.object,
            description=_("Activity added to camp %(code)s") % {"code": self.camp_day.camp.code},
        )
        messages.success(self.request, self.success_message)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return (
            reverse("surf_camps:detail", kwargs={"pk": self.camp_day.camp_id}) + "?tab=programme"
        )


class CampActivityUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "surf_camps.change"
    model = CampActivity
    form_class = CampActivityForm
    template_name = "surf_camps/campactivity_form.html"
    context_object_name = "activity"

    def get_queryset(self):
        return CampActivity.objects.select_related("camp_day", "camp_day__camp", "instructor")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["camp_day"] = self.object.camp_day
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.object.camp_day.camp
        context["day"] = self.object.camp_day
        context["title"] = _("Edit activity")
        return context

    def form_valid(self, form):
        clashes = services.activity_conflicts(form.instance)
        if clashes:
            form.add_error(
                None,
                _("%(instructor)s already has %(title)s at %(time)s that day.")
                % {
                    "instructor": form.instance.instructor,
                    "title": clashes[0].title,
                    "time": clashes[0].time_label,
                },
            )
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_success_url(self):
        return (
            reverse("surf_camps:detail", kwargs={"pk": self.object.camp_day.camp_id})
            + "?tab=programme"
        )


class CampActivityDeleteView(CapabilityRequiredMixin, AuditedDeleteMixin, DeleteView):
    capability = "surf_camps.change"
    model = CampActivity
    template_name = "surf_camps/campactivity_confirm_delete.html"
    context_object_name = "activity"
    success_message = _("Activity removed.")

    def get_queryset(self):
        return CampActivity.objects.select_related("camp_day", "camp_day__camp")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["camp"] = self.object.camp_day.camp
        context["day"] = self.object.camp_day
        return context

    def get_success_url(self):
        return (
            reverse("surf_camps:detail", kwargs={"pk": self.object.camp_day.camp_id})
            + "?tab=programme"
        )


# ---------------------------------------------------------------------------
# Roster & export
# ---------------------------------------------------------------------------
class CampRosterView(CapabilityRequiredMixin, StaffOnlyMixin, DetailView):
    """Printable day sheet: who is here, what happens, who needs watching.

    Names every child on site that day together with their medical and
    dietary flags. Staff-only, unconditionally.
    """

    capability = "surf_camps.view"
    model = SurfCamp
    template_name = "surf_camps/camp_roster.html"
    context_object_name = "camp"

    def get_queryset(self):
        return SurfCamp.objects.select_related("spot", "lead_instructor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        camp = self.object
        fallback = timezone.localdate() if camp.is_running else camp.start_date
        roster_date = _parse_date(self.request.GET.get("date"), fallback)
        context.update(_badge_context())
        context["roster"] = services.camp_daily_roster(camp, roster_date)
        context["roster_date"] = roster_date
        context["camp_dates"] = camp.date_list()
        return context


class CampParticipantExportView(CapabilityRequiredMixin, DetailView):
    """CSV of the participant list — the file the accommodation asks for."""

    capability = "surf_camps.export"
    model = SurfCamp

    def get(self, request, *args, **kwargs):
        camp = self.get_object()
        participants = participants_for(camp).select_related("student")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="camp_{camp.code}_participants.csv"'
        )
        response.write("﻿")  # BOM so Excel detects UTF-8

        writer = safe_csv_writer(response, delimiter=";")
        writer.writerow(
            [
                _("Student"),
                _("Status"),
                _("Room type"),
                _("Room"),
                _("Arrival"),
                _("Arrival flight"),
                _("Departure"),
                _("Departure flight"),
                _("Transfer"),
                _("Dietary requirements"),
                _("T-shirt"),
                _("Paid"),
                _("Balance"),
            ]
        )
        for participant in participants:
            writer.writerow(
                [
                    str(participant.student),
                    participant.get_status_display(),
                    participant.get_room_type_display(),
                    participant.room_number,
                    participant.arrival_datetime.strftime("%Y-%m-%d %H:%M")
                    if participant.arrival_datetime
                    else "",
                    participant.arrival_flight,
                    participant.departure_datetime.strftime("%Y-%m-%d %H:%M")
                    if participant.departure_datetime
                    else "",
                    participant.departure_flight,
                    _("Yes") if participant.needs_transfer else _("No"),
                    participant.dietary_requirements,
                    participant.get_t_shirt_size_display() if participant.t_shirt_size else "",
                    f"{participant.amount_paid:.2f}",
                    f"{participant.balance_due:.2f}",
                ]
            )

        record_audit(
            request,
            action=AuditAction.EXPORT,
            instance=camp,
            description=_("Participant list exported for camp %(code)s") % {"code": camp.code},
        )
        return response


__all__ = [
    "CampActivityCreateView",
    "CampActivityDeleteView",
    "CampActivityUpdateView",
    "CampCancelView",
    "CampDayCreateView",
    "CampDayDeleteView",
    "CampDayUpdateView",
    "CampGenerateProgrammeView",
    "CampParticipantExportView",
    "CampPublishView",
    "CampRosterView",
    "ParticipantCreateView",
    "ParticipantPanelView",
    "ParticipantRemoveView",
    "ParticipantStatusView",
    "ParticipantUpdateView",
    "SurfCampCreateView",
    "SurfCampDeleteView",
    "SurfCampDetailView",
    "SurfCampListView",
    "SurfCampUpdateView",
]
