"""Admin registrations for bookings.

The admin is a back-office repair tool, not the operational UI: status changes
made here bypass the seat and ratio rules, so the actions below are the ones a
manager needs when the front end cannot help, and each still writes an audit
entry through the service layer.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from . import services
from .models import Booking, WaitlistEntry


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "booking_code",
        "customer",
        "student",
        "booking_type",
        "status",
        "payment_status",
        "participants",
        "total_amount",
        "paid_amount",
        "booked_at",
    )
    list_filter = (
        "status",
        "payment_status",
        "booking_type",
        "source",
        "is_deleted",
        "booked_at",
    )
    search_fields = (
        "booking_code",
        "customer__first_name",
        "customer__last_name",
        "internal_notes",
        "special_requests",
    )
    # raw_id rather than autocomplete: it does not depend on another app's
    # ModelAdmin declaring search_fields.
    raw_id_fields = ("customer", "student", "lesson", "surf_camp")
    readonly_fields = (
        "booking_code",
        "public_id",
        "total_amount",
        "booked_at",
        "confirmed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )
    date_hierarchy = "booked_at"
    ordering = ("-booked_at",)
    list_select_related = ("customer", "student", "lesson", "surf_camp")
    actions = ("action_confirm", "action_complete", "action_recalculate")

    fieldsets = (
        (None, {"fields": ("booking_code", "booking_type", "status", "payment_status")}),
        (_("People"), {"fields": ("customer", "student")}),
        (_("Activity"), {"fields": ("lesson", "surf_camp", "participants")}),
        (
            _("Money"),
            {"fields": ("unit_price", "discount_amount", "total_amount", "paid_amount")},
        ),
        (
            _("Cancellation"),
            {"fields": ("cancelled_at", "cancellation_reason", "cancellation_fee")},
        ),
        (
            _("Operations"),
            {
                "fields": (
                    "source",
                    "booked_at",
                    "confirmed_at",
                    "special_requests",
                    "internal_notes",
                    "reminder_sent",
                    "reminder_sent_at",
                )
            },
        ),
        (
            _("Record"),
            {
                "classes": ("collapse",),
                "fields": (
                    "public_id",
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                    "is_deleted",
                    "deleted_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return Booking.all_objects.select_related(
            "customer", "student", "lesson", "surf_camp"
        )

    @admin.action(description=_("Confirm the selected bookings"))
    def action_confirm(self, request, queryset):
        done, failed = 0, 0
        for booking in queryset:
            try:
                services.confirm_booking(booking, user=request.user, request=request)
                done += 1
            except services.BookingError:
                failed += 1
        self.message_user(
            request,
            ngettext("%(n)d booking confirmed.", "%(n)d bookings confirmed.", done)
            % {"n": done},
            messages.SUCCESS if done else messages.WARNING,
        )
        if failed:
            self.message_user(
                request,
                ngettext(
                    "%(n)d booking could not be confirmed.",
                    "%(n)d bookings could not be confirmed.",
                    failed,
                )
                % {"n": failed},
                messages.WARNING,
            )

    @admin.action(description=_("Mark the selected bookings as completed"))
    def action_complete(self, request, queryset):
        done = 0
        for booking in queryset:
            try:
                services.complete_booking(booking, user=request.user, request=request)
                done += 1
            except services.BookingError:
                continue
        self.message_user(
            request,
            ngettext("%(n)d booking completed.", "%(n)d bookings completed.", done)
            % {"n": done},
            messages.SUCCESS if done else messages.WARNING,
        )

    @admin.action(description=_("Recalculate totals and payment status"))
    def action_recalculate(self, request, queryset):
        for booking in queryset:
            booking.recalculate_totals(commit=True)
        self.message_user(request, _("Totals recalculated."), messages.SUCCESS)


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = (
        "position",
        "customer",
        "student",
        "lesson",
        "surf_camp",
        "participants",
        "requested_at",
        "is_notified",
        "is_converted",
    )
    list_filter = ("is_converted", "is_notified", "requested_at")
    search_fields = ("customer__first_name", "customer__last_name", "note")
    raw_id_fields = ("customer", "student", "lesson", "surf_camp", "converted_booking")
    readonly_fields = ("public_id", "requested_at", "notified_at", "created_at", "updated_at")
    ordering = ("is_converted", "position")
    list_select_related = ("customer", "student", "lesson", "surf_camp")
    actions = ("action_promote",)

    @admin.action(description=_("Promote the next waiting entry into a booking"))
    def action_promote(self, request, queryset):
        created = 0
        for entry in queryset.filter(is_converted=False):
            booking = services.promote_from_waitlist(
                lesson=entry.lesson, camp=entry.surf_camp, user=request.user, request=request
            )
            if booking is not None:
                created += 1
        self.message_user(
            request,
            ngettext(
                "%(n)d booking created from the waiting list.",
                "%(n)d bookings created from the waiting list.",
                created,
            )
            % {"n": created},
            messages.SUCCESS if created else messages.WARNING,
        )
