from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import SkillAssessment, Student


class SkillAssessmentInline(admin.TabularInline):
    model = SkillAssessment
    extra = 0
    fields = (
        "assessed_on",
        "instructor",
        "level_before",
        "level_after",
        "paddling",
        "popup",
        "positioning",
        "wave_reading",
        "safety",
    )
    ordering = ("-assessed_on",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "student_code",
        "full_name",
        "surf_level",
        "can_swim",
        "total_lessons",
        "last_lesson_date",
        "is_active",
    )
    list_filter = ("surf_level", "is_active", "can_swim", "stance", "is_deleted")
    search_fields = (
        "student_code",
        "customer__customer_code",
        "customer__first_name",
        "customer__last_name",
    )
    ordering = ("customer__last_name", "customer__first_name")
    autocomplete_fields = ("customer",)
    inlines = (SkillAssessmentInline,)
    readonly_fields = (
        "student_code",
        "public_id",
        "total_lessons",
        "total_hours",
        "last_lesson_date",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("student_code", "public_id", "customer", "is_active", "joined_at")}),
        (
            _("Surfing"),
            {"fields": ("surf_level", "goals", "stance", "board_preference")},
        ),
        (_("Water competence"), {"fields": ("can_swim", "swim_distance_m")}),
        (
            _("Medical"),
            {"fields": ("medical_conditions", "medications", "allergies")},
        ),
        (
            _("Sizing"),
            {"fields": ("weight_kg", "height_cm", "shoe_size", "wetsuit_size")},
        ),
        (
            _("Progress"),
            {
                "fields": (
                    "preferred_instructor",
                    "total_lessons",
                    "total_hours",
                    "last_lesson_date",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        return Student.all_objects.select_related("customer")

    @admin.display(description=_("name"), ordering="customer__last_name")
    def full_name(self, obj: Student) -> str:
        return obj.full_name


@admin.register(SkillAssessment)
class SkillAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "assessed_on",
        "instructor",
        "level_before",
        "level_after",
        "average_score",
    )
    list_filter = ("level_after", "assessed_on")
    search_fields = (
        "student__student_code",
        "student__customer__first_name",
        "student__customer__last_name",
    )
    date_hierarchy = "assessed_on"
    autocomplete_fields = ("student",)
    ordering = ("-assessed_on",)

    def get_queryset(self, request):
        return SkillAssessment.all_objects.select_related(
            "student", "student__customer", "instructor"
        )

    @admin.display(description=_("average"))
    def average_score(self, obj: SkillAssessment) -> float:
        return obj.average_score
