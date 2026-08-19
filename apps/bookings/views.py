"""HTML views for the booking desk.

Views orchestrate and render; every rule that decides whether something may
happen lives in :mod:`apps.bookings.services`.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import FieldError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dates import WEEKDAYS
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView, UpdateView

from apps.accounts.permissions import CapabilityRequiredMixin, StaffOnlyMixin
from apps.core.enums import BookingSource, BookingStatus, PaymentStatus
from apps.core.mixins import (
    AuditedUpdateMixin,
    HtmxPartialMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
)

from . import selectors, services
from .forms import (
    BookingCancelForm,
    BookingCreateForm,
    BookingFilterForm,
    BookingPaymentForm,
    BookingUpdateForm,
    WaitlistEntryForm,
)
from .models import Booking, WaitlistEntry

#: Search paths used by the list screen, richest first.
SEARCH_FIELD_SETS = (
    (
        "booking_code",
        "customer__first_name",
        "customer__last_name",
        "customer__email",
        "customer__phone",
        "student__first_name",
        "student__last_name",
    ),
    ("booking_code", "customer__first_name", "customer__last_name"),
    ("booking_code",),
)


def _weekday_labels(first_day: int = 0) -> list:
    """Localised weekday headers, starting on Monday."""
    return [WEEKDAYS[(first_day + offset) % 7] for offset in range(7)]


def _calendar_filters(request) -> dict:
    def as_int(name):
        raw = (request.GET.get(name) or "").strip()
        return int(raw) if raw.isdigit() else None

    return {
        "lesson_type": as_int("lesson_type"),
        "instructor": as_int("instructor"),
        "location": as_int("location"),
        "status": (request.GET.get("lesson_status") or "").strip(),
        "only_available": request.GET.get("only_available") in {"1", "true", "on"},
        "q": (request.GET.get("q") or "").strip(),
    }


def _period_label(view: str, anchor, grid_start, grid_end) -> str:
    if view == "day":
        return formats.date_format(anchor, "l, j F Y")
    if view == "week":
        return "%s – %s" % (
            formats.date_format(grid_start, "j M"),
            formats.date_format(grid_end, "j M Y"),
        )
    return formats.date_format(anchor, "F Y")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
class BookingCalendarView(CapabilityRequiredMixin, StaffOnlyMixin, TemplateView):
    """Month / week / day schedule, navigated without a full page load.

    The whole shell (toolbar + grid) is the HTMX target, so the previous/next
    buttons keep working after every swap.
    """

    capability = "bookings.view"
    template_name = "bookings/booking_calendar.html"
    partial_template_name = "bookings/partials/calendar_shell.html"

    def get_template_names(self):
        if getattr(self.request, "htmx", False):
            return [self.partial_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        view = (self.request.GET.get("view") or "month").strip()
        if view not in {"month", "week", "day"}:
            view = "month"
        anchor = selectors.parse_anchor(self.request.GET.get("date", ""))
        filters = _calendar_filters(self.request)

        calendar = services.build_calendar(view, anchor, filters)
        context.update(calendar)
        context["filters"] = filters
        context["weekday_labels"] = _weekday_labels()
        context["period_label"] = _period_label(
            view, anchor, calendar["grid_start"], calendar["grid_end"]
        )
        context["today"] = timezone.localdate()
        context["calendar_url"] = reverse("bookings:calendar")
        context["lesson_types"] = _lesson_types()
        context["instructors"] = _instructors()
        context["status_colours"] = services.STATUS_COLOURS
        return context


def _lesson_types():
    model = services.get_model("lessons", "LessonType")
    if model is None:
        return []
    try:
        return list(model.objects.all()[:100])
    except Exception:  # noqa: BLE001 - table not migrated yet
        return []


def _instructors():
    model = services.get_model("instructors", "Instructor")
    if model is None:
        return []
    try:
        return list(model.objects.all()[:200])
    except Exception:  # noqa: BLE001
        return []


class DailyScheduleView(CapabilityRequiredMixin, StaffOnlyMixin, TemplateView):
    """The beach team's run sheet for one day."""

    capability = "bookings.view"
    template_name = "bookings/schedule_day.html"
    partial_template_name = "bookings/partials/schedule_body.html"

    def get_template_names(self):
        if getattr(self.request, "htmx", False):
            return [self.partial_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        day = selectors.parse_anchor(self.request.GET.get("date", ""))
        context["schedule"] = services.daily_schedule(day)
        context["day"] = day
        context["previous_day"] = day - timedelta(days=1)
        context["next_day"] = day + timedelta(days=1)
        context["day_label"] = formats.date_format(day, "l, j F Y")
        return context


# ---------------------------------------------------------------------------
# List & detail
# ---------------------------------------------------------------------------
class BookingListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    capability = "bookings.view"
    model = Booking
    queryset = selectors.base_queryset()
    template_name = "bookings/booking_list.html"
    partial_template_name = "bookings/partials/booking_table.html"
    context_object_name = "bookings"
    paginate_by = 25
    owner_lookup = "customer__user"
    search_fields = SEARCH_FIELD_SETS[0]

    def apply_search(self, queryset):
        """Search across customer and student names, degrading gracefully."""
        term = self.get_search_term()
        if not term:
            return queryset
        for fields in SEARCH_FIELD_SETS:
            condition = Q()
            for field in fields:
                condition |= Q(**{f"{field}__icontains": term})
            try:
                filtered = queryset.filter(condition)
                str(filtered.query)
                return filtered
            except (FieldError, ValueError, TypeError):
                continue
        return queryset.filter(booking_code__icontains=term)

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset, self.applied_filters = selectors.filter_bookings(queryset, self.request.GET)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["applied"] = getattr(self, "applied_filters", {})
        context["filter_form"] = BookingFilterForm(self.request.GET or None)
        context["summary"] = services.booking_summary(self.object_list)
        context["status_choices"] = BookingStatus.choices
        context["payment_choices"] = PaymentStatus.choices
        context["type_choices"] = Booking.BookingType.choices
        context["source_choices"] = BookingSource.choices
        return context


class BookingDetailView(CapabilityRequiredMixin, OwnerScopedQuerysetMixin, DetailView):
    capability = "bookings.view"
    model = Booking
    template_name = "bookings/booking_detail.html"
    context_object_name = "booking"
    owner_lookup = "customer__user"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "customer", "student", "lesson", "surf_camp", "created_by", "updated_by"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = self.object
        context["timeline"] = services.booking_timeline(booking)
        context["suggested_fee"] = services.cancellation_fee_for(booking)
        context["payment_form"] = BookingPaymentForm(balance=booking.balance_due)
        context["status_colours"] = services.STATUS_COLOURS

        if booking.lesson_id:
            context["seats_taken"] = services.seats_taken(lesson=booking.lesson)
            context["seats_capacity"] = services.lesson_capacity(booking.lesson)
            context["waitlist"] = services.waitlist_for(lesson=booking.lesson)[:10]
            context["roster"] = (
                selectors.base_queryset()
                .filter(lesson=booking.lesson)
                .exclude(pk=booking.pk)
                .exclude(status=BookingStatus.CANCELLED)[:25]
            )
        elif booking.surf_camp_id:
            context["seats_taken"] = services.seats_taken(camp=booking.surf_camp)
            context["seats_capacity"] = services.camp_capacity(booking.surf_camp)
            context["waitlist"] = services.waitlist_for(camp=booking.surf_camp)[:10]
            context["roster"] = (
                selectors.base_queryset()
                .filter(surf_camp=booking.surf_camp)
                .exclude(pk=booking.pk)
                .exclude(status=BookingStatus.CANCELLED)[:25]
            )
        else:
            context["waitlist"] = []
            context["roster"] = []

        if booking.is_active:
            context["live_warnings"] = services.check_booking_conflicts(
                booking_type=booking.booking_type,
                lesson=booking.lesson,
                camp=booking.surf_camp,
                student=booking.student,
                participants=booking.participants,
                customer=booking.customer,
                exclude_booking=booking,
            )
        else:
            context["live_warnings"] = []
        return context


class BookingUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "bookings.change"
    model = Booking
    form_class = BookingUpdateForm
    template_name = "bookings/booking_form.html"
    context_object_name = "booking"
    success_message = _lazy("Booking updated.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_update"] = True
        context["title"] = _("Edit booking %(code)s") % {"code": self.object.booking_code}
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.recalculate_totals(commit=True)
        return response

    def get_success_url(self):
        return reverse("bookings:detail", kwargs={"pk": self.object.pk})


# ---------------------------------------------------------------------------
# Creation flow
# ---------------------------------------------------------------------------
class BookingCreateView(CapabilityRequiredMixin, TemplateView):
    """One screen that feels like three steps: customer → session → confirm."""

    capability = "bookings.add"
    template_name = "bookings/booking_form.html"

    def get_initial(self) -> dict:
        initial: dict = {"participants": 1, "source": BookingSource.WALK_IN}
        lesson_id = (self.request.GET.get("lesson") or "").strip()
        camp_id = (self.request.GET.get("surf_camp") or "").strip()
        customer_id = (self.request.GET.get("customer") or "").strip()
        if lesson_id.isdigit():
            initial["lesson"] = int(lesson_id)
            initial["booking_type"] = Booking.BookingType.LESSON
        if camp_id.isdigit():
            initial["surf_camp"] = int(camp_id)
            initial["booking_type"] = Booking.BookingType.CAMP
        if customer_id.isdigit():
            initial["customer"] = int(customer_id)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", BookingCreateForm(initial=self.get_initial()))
        context["is_update"] = False
        context["title"] = _("New booking")
        context["conflicts"] = kwargs.get("conflicts", [])
        context["available_lessons"] = services.available_lessons(limit=15)
        context["check_url"] = reverse("bookings:check")
        return context

    def post(self, request, *args, **kwargs):
        form = BookingCreateForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        data = form.cleaned_data
        status = (
            BookingStatus.CONFIRMED
            if data.get("confirm_immediately")
            else BookingStatus.PENDING
        )
        try:
            booking = services.create_booking(
                data["customer"],
                data["booking_type"],
                lesson=data.get("lesson"),
                camp=data.get("surf_camp"),
                student=data.get("student"),
                participants=data["participants"],
                source=data["source"],
                user=request.user,
                request=request,
                unit_price=data.get("unit_price") or None,
                discount_amount=data.get("discount_amount"),
                special_requests=data.get("special_requests", ""),
                internal_notes=data.get("internal_notes", ""),
                status=status,
            )
        except services.BookingConflictError as error:
            return self.render_to_response(
                self.get_context_data(form=form, conflicts=error.errors)
            )
        except services.BookingError as error:
            return self.render_to_response(
                self.get_context_data(form=form, conflicts=[error.message])
            )

        messages.success(
            request,
            _("Booking %(code)s created for %(customer)s.")
            % {"code": booking.booking_code, "customer": booking.customer},
        )
        return redirect("bookings:detail", pk=booking.pk)


class BookingConflictCheckView(CapabilityRequiredMixin, View):
    """HTMX endpoint that re-runs the rules as the form is filled in."""

    capability = "bookings.add"

    def post(self, request, *args, **kwargs):
        form = BookingCreateForm(request.POST)
        form.is_valid()  # populate cleaned_data; partial forms are expected here
        data = getattr(form, "cleaned_data", {})

        lesson = data.get("lesson")
        camp = data.get("surf_camp")
        student = data.get("student")
        customer = data.get("customer")
        participants = data.get("participants") or 1
        booking_type = data.get("booking_type") or Booking.BookingType.LESSON

        conflicts: list[str] = []
        if lesson is not None or camp is not None:
            conflicts = services.check_booking_conflicts(
                booking_type=booking_type,
                lesson=lesson,
                camp=camp,
                student=student,
                participants=participants,
                customer=customer,
            )

        target = lesson or camp
        capacity = (
            services.lesson_capacity(lesson)
            if lesson is not None
            else (services.camp_capacity(camp) if camp is not None else 0)
        )
        taken = services.seats_taken(lesson=lesson, camp=camp) if target is not None else 0
        unit_price = data.get("unit_price") or services.default_unit_price(
            lesson=lesson, camp=camp
        )
        discount = data.get("discount_amount") or 0

        return render(
            request,
            "bookings/partials/conflict_panel.html",
            {
                "conflicts": conflicts,
                "target": target,
                "has_target": target is not None,
                "capacity": capacity,
                "taken": taken,
                "free": max(0, capacity - taken),
                "capacity_label": f"{taken}/{capacity}" if capacity else "—",
                "participants": participants,
                "unit_price": unit_price,
                "estimated_total": max(
                    0, (unit_price or 0) * participants - (discount or 0)
                ),
                "form_errors": form.errors,
            },
        )


class CustomerSearchView(CapabilityRequiredMixin, View):
    """HTMX customer lookup for the booking form."""

    capability = "bookings.add"

    def get(self, request, *args, **kwargs):
        term = (request.GET.get("customer_q") or request.GET.get("q") or "").strip()
        return render(
            request,
            "bookings/partials/customer_results.html",
            {"results": services.search_customers(term), "term": term},
        )


class StudentOptionsView(CapabilityRequiredMixin, View):
    """HTMX student list for the chosen customer."""

    capability = "bookings.add"

    def get(self, request, *args, **kwargs):
        raw = (request.GET.get("customer") or "").strip()
        customer = None
        if raw.isdigit():
            model = services.get_model("customers", "Customer")
            if model is not None:
                customer = model.objects.filter(pk=int(raw)).first()
        return render(
            request,
            "bookings/partials/student_options.html",
            {"students": services.students_for_customer(customer), "customer": customer},
        )


class LessonPickerView(CapabilityRequiredMixin, View):
    """HTMX list of sessions that still have a free seat."""

    capability = "bookings.add"

    def get(self, request, *args, **kwargs):
        term = (request.GET.get("lesson_q") or request.GET.get("q") or "").strip()
        day_raw = (request.GET.get("lesson_date") or "").strip()
        day = selectors.parse_date(day_raw)
        return render(
            request,
            "bookings/partials/lesson_options.html",
            {
                "lessons": services.available_lessons(search=term, day=day),
                "term": term,
                "day": day,
            },
        )


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------
class BookingActionView(CapabilityRequiredMixin, View):
    """Base for the guarded POST-only action buttons on the detail screen."""

    capability = "bookings.change"
    success_message = _lazy("Booking updated.")

    def perform(self, booking):  # pragma: no cover - overridden
        raise NotImplementedError

    def post(self, request, pk, *args, **kwargs):
        booking = get_object_or_404(Booking, pk=pk)
        try:
            self.perform(booking)
        except services.BookingConflictError as error:
            for problem in error.errors:
                messages.error(request, problem)
        except services.BookingError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, self.success_message)
        return redirect("bookings:detail", pk=booking.pk)


class BookingConfirmView(BookingActionView):
    capability = "bookings.change"
    success_message = _lazy("Booking confirmed.")

    def perform(self, booking):
        services.confirm_booking(booking, user=self.request.user, request=self.request)


class BookingCheckInView(BookingActionView):
    capability = "bookings.change"
    success_message = _lazy("Customer checked in.")

    def perform(self, booking):
        force = self.request.POST.get("force") in {"1", "true", "on"}
        services.check_in_booking(
            booking, user=self.request.user, request=self.request, force=force
        )


class BookingCompleteView(BookingActionView):
    capability = "bookings.change"
    success_message = _lazy("Booking completed.")

    def perform(self, booking):
        services.complete_booking(booking, user=self.request.user, request=self.request)


class BookingNoShowView(BookingActionView):
    capability = "bookings.change"
    success_message = _lazy("Booking marked as a no-show.")

    def perform(self, booking):
        services.mark_no_show(booking, user=self.request.user, request=self.request)


class BookingPaymentView(BookingActionView):
    capability = "bookings.change"
    success_message = _lazy("Payment recorded.")

    def post(self, request, pk, *args, **kwargs):
        booking = get_object_or_404(Booking, pk=pk)
        form = BookingPaymentForm(request.POST, balance=booking.balance_due)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("bookings:detail", pk=booking.pk)
        try:
            services.register_payment(
                booking, form.cleaned_data["amount"], user=request.user, request=request
            )
        except services.BookingError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, self.success_message)
        return redirect("bookings:detail", pk=booking.pk)


class BookingCancelView(CapabilityRequiredMixin, View):
    """Confirm screen plus the POST that actually cancels."""

    capability = "bookings.change"
    template_name = "bookings/booking_cancel.html"

    def load(self, request, pk):
        """Return ``(booking, redirect_response)`` — one of the two is ``None``."""
        booking = get_object_or_404(Booking, pk=pk)
        if not booking.can_cancel:
            messages.error(
                request,
                _("Booking %(code)s is %(status)s and can no longer be cancelled.")
                % {"code": booking.booking_code, "status": booking.get_status_display()},
            )
            return booking, redirect("bookings:detail", pk=booking.pk)
        return booking, None

    def get(self, request, pk, *args, **kwargs):
        booking, bail = self.load(request, pk)
        if bail is not None:
            return bail
        suggested = services.cancellation_fee_for(booking)
        return render(
            request,
            self.template_name,
            {
                "booking": booking,
                "form": BookingCancelForm(suggested_fee=suggested),
                "suggested_fee": suggested,
                "waitlist": (
                    services.waitlist_for(lesson=booking.lesson, camp=booking.surf_camp)[:5]
                    if (booking.lesson_id or booking.surf_camp_id)
                    else []
                ),
            },
        )

    def post(self, request, pk, *args, **kwargs):
        booking, bail = self.load(request, pk)
        if bail is not None:
            return bail
        suggested = services.cancellation_fee_for(booking)
        form = BookingCancelForm(request.POST, suggested_fee=suggested)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"booking": booking, "form": form, "suggested_fee": suggested, "waitlist": []},
            )
        try:
            services.cancel_booking(
                booking,
                form.full_reason,
                user=request.user,
                fee=form.resolved_fee,
                request=request,
            )
        except services.BookingError as error:
            messages.error(request, error.message)
            return redirect("bookings:detail", pk=booking.pk)

        messages.success(
            request,
            _("Booking %(code)s cancelled. Fee charged: %(fee)s.")
            % {"code": booking.booking_code, "fee": booking.cancellation_fee},
        )
        return redirect("bookings:detail", pk=booking.pk)


# ---------------------------------------------------------------------------
# Waiting list
# ---------------------------------------------------------------------------
class WaitlistListView(
    CapabilityRequiredMixin, OwnerScopedQuerysetMixin, HtmxPartialMixin, ListView
):
    capability = "bookings.view"
    owner_lookups = ("customer__user", "student__customer__user")
    model = WaitlistEntry
    template_name = "bookings/waitlist_list.html"
    partial_template_name = "bookings/partials/waitlist_table.html"
    context_object_name = "entries"
    paginate_by = 25

    def get_queryset(self):
        queryset, self.applied_filters = selectors.filter_waitlist(
            self.scope(selectors.waitlist_queryset()), self.request.GET
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["applied"] = getattr(self, "applied_filters", {})
        context["form"] = WaitlistEntryForm()
        return context


class WaitlistCreateView(CapabilityRequiredMixin, View):
    capability = "bookings.add"

    def post(self, request, *args, **kwargs):
        form = WaitlistEntryForm(request.POST)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return redirect("bookings:waitlist")
        data = form.cleaned_data
        try:
            entry = services.add_to_waitlist(
                data["customer"],
                lesson=data.get("lesson"),
                camp=data.get("surf_camp"),
                student=data.get("student"),
                participants=data.get("participants") or 1,
                note=data.get("note", ""),
                user=request.user,
                request=request,
            )
        except services.BookingError as error:
            messages.error(request, error.message)
        else:
            messages.success(
                request,
                _("%(customer)s is on the waiting list at position %(pos)s.")
                % {"customer": entry.customer, "pos": entry.position},
            )
        return redirect("bookings:waitlist")


class WaitlistPromoteView(CapabilityRequiredMixin, View):
    """Turn the next waiting entry into a held booking."""

    capability = "bookings.change"

    def post(self, request, pk, *args, **kwargs):
        entry = get_object_or_404(WaitlistEntry, pk=pk)
        booking = services.promote_from_waitlist(
            lesson=entry.lesson, camp=entry.surf_camp, user=request.user, request=request
        )
        if booking is None:
            messages.warning(
                request,
                _("No waiting entry could be promoted — the session is still unavailable."),
            )
            return redirect("bookings:waitlist")
        messages.success(
            request,
            _("Booking %(code)s created from the waiting list. Confirm it with the customer.")
            % {"code": booking.booking_code},
        )
        return redirect("bookings:detail", pk=booking.pk)


class WaitlistRemoveView(CapabilityRequiredMixin, View):
    capability = "bookings.delete"

    def post(self, request, pk, *args, **kwargs):
        entry = get_object_or_404(WaitlistEntry, pk=pk)
        entry.delete()
        messages.success(request, _("Removed from the waiting list."))
        return redirect("bookings:waitlist")
