"""Admin registrations for the lesson catalogue, timetable and roster."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Lesson, LessonAttendance, LessonType


@admin.register(LessonType)
class LessonTypeAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "category",
        "level_band",
        "duration_minutes",
        "max_students",
        "base_price",
        "is_active",
        "sort_order",
    )
    list_filter = ("category", "is_active", "min_level", "max_level", "requires_board")
    search_fields = ("code", "name", "description")
    ordering = ("sort_order", "name")
    list_editable = ("is_active", "sort_order")
    readonly_fields = ("public_id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("code", "name", "category", "description", "is_active", "sort_order")}),
        (_("Who it is for"), {"fields": ("min_level", "max_level", "min_age", "max_age")}),
        (
            _("Format"),
            {"fields": ("duration_minutes", "min_students", "max_students", "colour")},
        ),
        (_("Price"), {"fields": ("base_price", "price_per_extra_student")}),
        (
            _("Equipment"),
            {"fields": ("requires_board", "requires_wetsuit", "requires_leash")},
        ),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    @admin.display(description=_("levels"))
    def level_band(self, obj: LessonType) -> str:
        return f"{obj.get_min_level_display()} → {obj.get_max_level_display()}"


class LessonAttendanceInline(admin.TabularInline):
    model = LessonAttendance
    extra = 0
    fields = (
        "student",
        "status",
        "booking",
        "assigned_board",
        "assigned_wetsuit",
        "rating",
        "checked_in_at",
    )
    readonly_fields = ("checked_in_at",)
    # raw_id rather than autocomplete: autocomplete would require the students
    # admin to declare search_fields, which is not this module's to guarantee.
    raw_id_fields = ("student", "booking", "assigned_board", "assigned_wetsuit")
    show_change_link = True


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = (
        "lesson_code",
        "date",
        "time_label",
        "lesson_type",
        "spot",
        "instructor",
        "seats",
        "status",
        "safety_briefing_done",
    )
    list_filter = ("status", "date", "lesson_type", "spot", "safety_briefing_done")
    search_fields = ("lesson_code", "notes", "internal_notes", "lesson_type__name")
    date_hierarchy = "date"
    ordering = ("-date", "start_time")
    autocomplete_fields = ("lesson_type",)
    filter_horizontal = ("assistant_instructors",)
    inlines = (LessonAttendanceInline,)
    readonly_fields = (
        "lesson_code",
        "public_id",
        "conditions_snapshot",
        "safety_checked_by",
        "safety_checked_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("lesson_code", "lesson_type", "spot", "status")}),
        (_("When"), {"fields": ("date", "start_time", "end_time")}),
        (_("Who"), {"fields": ("instructor", "assistant_instructors", "capacity")}),
        (_("Money"), {"fields": ("price_override",)}),
        (_("Notes"), {"fields": ("notes", "internal_notes")}),
        (
            _("Safety"),
            {
                "fields": (
                    "safety_briefing_done",
                    "safety_checked_by",
                    "safety_checked_at",
                    "conditions_snapshot",
                )
            },
        ),
        (_("Cancellation"), {"fields": ("cancellation_reason", "cancelled_at")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("lesson_type", "spot", "instructor")
        )

    @admin.display(description=_("seats"))
    def seats(self, obj: Lesson) -> str:
        booked = obj.booked_count
        colour = "#e11d48" if booked >= obj.capacity else "#0f766e"
        return format_html(
            '<span style="color:{}">{}/{}</span>', colour, booked, obj.capacity
        )


@admin.register(LessonAttendance)
class LessonAttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "lesson",
        "student",
        "status",
        "checked_in_at",
        "assigned_board",
        "assigned_wetsuit",
        "rating",
    )
    list_filter = ("status", "lesson__date", "lesson__lesson_type")
    search_fields = ("lesson__lesson_code", "instructor_notes", "student_feedback")
    autocomplete_fields = ("lesson",)
    raw_id_fields = ("student", "booking", "assigned_board", "assigned_wetsuit")
    ordering = ("-lesson__date",)
    readonly_fields = ("public_id", "checked_in_at", "created_at", "updated_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("lesson", "student", "assigned_board", "assigned_wetsuit")
        )
