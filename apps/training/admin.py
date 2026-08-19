from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import TrainingCourse, TrainingLesson, TrainingProgress, TrainingStep


class TrainingLessonInline(admin.TabularInline):
    model = TrainingLesson
    extra = 0
    fields = ("order", "title_en", "title_tr", "estimated_minutes")
    ordering = ("order",)
    show_change_link = True


class TrainingStepInline(admin.TabularInline):
    model = TrainingStep
    extra = 0
    fields = ("order", "title_en", "title_tr", "target_url")
    ordering = ("order",)
    show_change_link = True


@admin.register(TrainingCourse)
class TrainingCourseAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title_en",
        "difficulty",
        "estimated_minutes",
        "lesson_count",
        "required_capability",
        "sort_order",
        "is_active",
    )
    list_filter = ("is_active", "difficulty", "required_capability")
    list_editable = ("sort_order", "is_active")
    search_fields = ("code", "title_en", "title_tr", "description_en", "description_tr")
    ordering = ("sort_order", "code")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = [TrainingLessonInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "code",
                    "icon",
                    "difficulty",
                    "estimated_minutes",
                    "required_capability",
                    "sort_order",
                    "is_active",
                )
            },
        ),
        (_("English"), {"fields": ("title_en", "description_en")}),
        (_("Türkçe"), {"fields": ("title_tr", "description_tr")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return TrainingCourse.all_objects.all()

    @admin.display(description=_("lessons"))
    def lesson_count(self, obj) -> int:
        return obj.lesson_count


@admin.register(TrainingLesson)
class TrainingLessonAdmin(admin.ModelAdmin):
    list_display = ("course", "order", "title_en", "title_tr", "estimated_minutes", "step_count")
    list_filter = ("course",)
    search_fields = ("title_en", "title_tr", "summary_en", "summary_tr", "course__code")
    autocomplete_fields = ("course",)
    ordering = ("course__sort_order", "order")
    inlines = [TrainingStepInline]

    fieldsets = (
        (None, {"fields": ("course", "order", "estimated_minutes")}),
        (_("English"), {"fields": ("title_en", "summary_en")}),
        (_("Türkçe"), {"fields": ("title_tr", "summary_tr")}),
    )

    @admin.display(description=_("steps"))
    def step_count(self, obj) -> int:
        return obj.step_count


@admin.register(TrainingStep)
class TrainingStepAdmin(admin.ModelAdmin):
    list_display = ("lesson", "order", "title_en", "target_url")
    list_filter = ("lesson__course",)
    search_fields = ("title_en", "title_tr", "body_en", "body_tr", "target_url")
    autocomplete_fields = ("lesson",)
    ordering = ("lesson__course__sort_order", "lesson__order", "order")

    fieldsets = (
        (None, {"fields": ("lesson", "order", "target_url", "image")}),
        (_("English"), {"fields": ("title_en", "body_en", "action_hint_en")}),
        (_("Türkçe"), {"fields": ("title_tr", "body_tr", "action_hint_tr")}),
    )


@admin.register(TrainingProgress)
class TrainingProgressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "course",
        "status",
        "percent_display",
        "started_at",
        "completed_at",
        "last_activity_at",
    )
    list_filter = ("status", "course")
    search_fields = ("user__username", "user__email", "course__code", "course__title_en")
    autocomplete_fields = ("course",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-last_activity_at",)

    @admin.display(description=_("complete"))
    def percent_display(self, obj) -> str:
        return f"{obj.percent_complete}%"
