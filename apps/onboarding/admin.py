from __future__ import annotations

from django.contrib import admin

from .models import OnboardingState


@admin.register(OnboardingState)
class OnboardingStateAdmin(admin.ModelAdmin):
    list_display = ("__str__", "school_name", "is_completed", "percent_complete", "completed_at")
    readonly_fields = ("completed_steps", "percent_complete", "completed_at")

    def has_add_permission(self, request) -> bool:
        # A singleton — created on first access by OnboardingState.get_state().
        return not OnboardingState.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
