from __future__ import annotations

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)


class CertificationInline(admin.TabularInline):
    model = Certification
    extra = 0
    fields = ("kind", "name", "issuing_body", "issued_on", "expires_on", "is_verified")
    ordering = ("expires_on",)
    show_change_link = True


class AvailabilitySlotInline(admin.TabularInline):
    model = AvailabilitySlot
    extra = 0
    fields = ("weekday", "start_time", "end_time", "is_active", "valid_from", "valid_until")
    ordering = ("weekday", "start_time")


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = (
        "instructor_code",
        "full_name",
        "max_level_taught",
        "max_students_per_lesson",
        "is_active",
        "is_available_for_booking",
        "rating_average",
        "total_lessons_taught",
    )
    list_filter = ("is_active", "is_available_for_booking", "max_level_taught", "hire_date")
    search_fields = (
        "instructor_code",
        "user__first_name",
        "user__last_name",
        "user__email",
    )
    autocomplete_fields = ("user",)
    readonly_fields = (
        "instructor_code",
        "public_id",
        "rating_average",
        "rating_count",
        "total_lessons_taught",
        "created_at",
        "updated_at",
    )
    inlines = (CertificationInline, AvailabilitySlotInline)
    fieldsets = (
        (None, {"fields": ("user", "instructor_code", "photo", "bio")}),
        (
            _("Teaching"),
            {
                "fields": (
                    "max_level_taught",
                    "max_students_per_lesson",
                    "specialties",
                    "languages",
                )
            },
        ),
        (_("Pay"), {"fields": ("hourly_rate", "commission_percent")}),
        (
            _("Status"),
            {"fields": ("hire_date", "is_active", "is_available_for_booking")},
        ),
        (
            _("Statistics"),
            {"fields": ("rating_average", "rating_count", "total_lessons_taught")},
        ),
        (
            _("Emergency contact"),
            {"fields": ("emergency_contact_name", "emergency_contact_phone")},
        ),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    @admin.display(description=_("name"), ordering="user__first_name")
    def full_name(self, obj) -> str:
        return obj.full_name

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "instructor",
        "kind",
        "issued_on",
        "expires_on",
        "is_verified",
        "expiry_state",
    )
    list_filter = ("kind", "is_verified", "expires_on")
    search_fields = (
        "name",
        "issuing_body",
        "certificate_number",
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
    )
    autocomplete_fields = ("instructor", "verified_by")
    readonly_fields = ("public_id", "verified_at", "created_at", "updated_at")
    date_hierarchy = "issued_on"

    @admin.display(description=_("state"))
    def expiry_state(self, obj) -> str:
        return str(obj.status_label)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("instructor", "instructor__user")


@admin.register(AvailabilitySlot)
class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "weekday",
        "start_time",
        "end_time",
        "is_active",
        "valid_from",
        "valid_until",
    )
    list_filter = ("weekday", "is_active")
    search_fields = (
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
    )
    autocomplete_fields = ("instructor",)
    ordering = ("instructor", "weekday", "start_time")


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "start_date",
        "end_date",
        "reason",
        "is_approved",
        "approved_by",
    )
    list_filter = ("is_approved", "reason", "start_date")
    search_fields = (
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
        "note",
    )
    autocomplete_fields = ("instructor", "approved_by")
    readonly_fields = ("public_id", "approved_at", "created_at", "updated_at")
    date_hierarchy = "start_date"
    actions = ("approve_selected",)

    @admin.action(description=_("Approve selected time off"))
    def approve_selected(self, request, queryset):
        updated = queryset.filter(is_approved=False).update(
            is_approved=True, approved_by=request.user, approved_at=timezone.now()
        )
        self.message_user(
            request,
            _("%(count)s absence record(s) approved.") % {"count": updated},
        )


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "period_start",
        "period_end",
        "reviewer",
        "overall_score",
    )
    list_filter = ("period_end", "reviewer")
    search_fields = (
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
        "strengths",
        "improvements",
    )
    autocomplete_fields = ("instructor", "reviewer")
    readonly_fields = ("public_id", "overall_score", "created_at", "updated_at")
    date_hierarchy = "period_end"
