from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import PasswordResetRequest, User, UserSession


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "get_display_name", "email", "role", "is_active", "last_login")
    list_filter = ("role", "is_active", "is_staff", "language")
    search_fields = ("username", "email", "first_name", "last_name", "employee_id")
    ordering = ("first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email", "phone", "photo")}),
        (_("Role & access"), {"fields": ("role", "extra_capabilities", "denied_capabilities")}),
        (_("Employment"), {"fields": ("job_title", "employee_id", "language", "notes")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "must_change_password",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "last_seen_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "role", "password1", "password2"),
            },
        ),
    )
    readonly_fields = ("last_seen_at",)


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "login_at", "logout_at", "ip_address", "was_successful")
    list_filter = ("was_successful", "login_at")
    search_fields = ("user__username", "user__email", "ip_address")
    readonly_fields = tuple(f.name for f in UserSession._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "used_at", "requested_ip")
    readonly_fields = tuple(f.name for f in PasswordResetRequest._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False
