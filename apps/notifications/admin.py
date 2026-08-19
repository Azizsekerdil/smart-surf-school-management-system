from __future__ import annotations

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Notification, NotificationPreference, NotificationTemplate


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "recipient",
        "category",
        "level",
        "title",
        "is_read",
        "is_emailed",
    )
    list_filter = ("category", "level", "is_read", "is_emailed", "created_at")
    search_fields = ("title", "body", "recipient__username", "recipient__email")
    date_hierarchy = "created_at"
    autocomplete_fields = ("recipient",)
    readonly_fields = ("public_id", "created_at", "updated_at", "read_at", "emailed_at")
    list_select_related = ("recipient",)
    actions = ("mark_selected_read", "mark_selected_unread")
    fieldsets = (
        (None, {"fields": ("recipient", "category", "level", "title", "body", "link_url")}),
        (
            _("Delivery"),
            {"fields": ("is_read", "read_at", "is_emailed", "emailed_at")},
        ),
        (
            _("Related record"),
            {
                "fields": ("related_object_type", "related_object_id"),
                "description": _(
                    "A deliberately soft reference: deleting the target never breaks "
                    "the notification history."
                ),
            },
        ),
        (_("Audit"), {"fields": ("public_id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.action(description=_("Mark selected notifications as read"))
    def mark_selected_read(self, request, queryset):
        updated = queryset.filter(is_read=False).update(
            is_read=True, read_at=timezone.now(), updated_at=timezone.now()
        )
        self.message_user(request, _("%(count)s marked as read.") % {"count": updated})

    @admin.action(description=_("Mark selected notifications as unread"))
    def mark_selected_unread(self, request, queryset):
        updated = queryset.filter(is_read=True).update(
            is_read=False, read_at=None, updated_at=timezone.now()
        )
        self.message_user(request, _("%(count)s marked as unread.") % {"count": updated})


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("code", "category", "level", "title_en", "is_active", "updated_at")
    list_filter = ("category", "level", "is_active")
    search_fields = ("code", "title_en", "title_tr", "body_en", "body_tr")
    prepopulated_fields = {"code": ("title_en",)}
    fieldsets = (
        (None, {"fields": ("code", "category", "level", "is_active")}),
        (_("English"), {"fields": ("title_en", "body_en")}),
        (_("Turkish"), {"fields": ("title_tr", "body_tr")}),
    )


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "in_app_enabled",
        "email_enabled",
        "quiet_hours_start",
        "quiet_hours_end",
        "updated_at",
    )
    list_filter = ("in_app_enabled", "email_enabled")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
