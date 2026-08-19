from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .models import HelpArticle, HelpCategory


class HelpArticleInline(admin.TabularInline):
    model = HelpArticle
    extra = 0
    fields = ("title_en", "title_tr", "sort_order", "is_published", "related_module")
    show_change_link = True
    ordering = ("sort_order", "title_en")


@admin.register(HelpCategory)
class HelpCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name_en", "name_tr", "icon", "sort_order", "is_active")
    list_filter = ("is_active",)
    list_editable = ("sort_order", "is_active")
    search_fields = ("code", "name_en", "name_tr")
    ordering = ("sort_order", "code")
    inlines = [HelpArticleInline]

    fieldsets = (
        (None, {"fields": ("code", "icon", "sort_order", "is_active")}),
        (_("English"), {"fields": ("name_en",)}),
        (_("Türkçe"), {"fields": ("name_tr",)}),
    )


@admin.register(HelpArticle)
class HelpArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title_en",
        "category",
        "related_module",
        "sort_order",
        "is_published",
        "view_count",
        "helpful_count",
        "updated_at",
    )
    list_filter = ("is_published", "category", "related_module")
    list_editable = ("sort_order", "is_published")
    search_fields = ("slug", "title_en", "title_tr", "keywords", "body_en", "body_tr")
    autocomplete_fields = ("category",)
    ordering = ("category__sort_order", "sort_order", "title_en")
    readonly_fields = (
        "public_id",
        "slug",
        "view_count",
        "helpful_count",
        "not_helpful_count",
        "created_at",
        "updated_at",
    )
    actions = ("publish_articles", "unpublish_articles")

    fieldsets = (
        (None, {"fields": ("category", "slug", "related_module", "keywords", "sort_order", "is_published")}),
        (_("English"), {"fields": ("title_en", "body_en")}),
        (_("Türkçe"), {"fields": ("title_tr", "body_tr")}),
        (
            _("Record"),
            {
                "fields": (
                    "public_id",
                    "view_count",
                    "helpful_count",
                    "not_helpful_count",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_queryset(self, request):
        # Archived articles must stay reachable so an editor can restore one.
        return HelpArticle.all_objects.select_related("category")

    @admin.action(description=_("Publish the selected articles"))
    def publish_articles(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(
            request,
            ngettext("%(count)d article published.", "%(count)d articles published.", updated)
            % {"count": updated},
        )

    @admin.action(description=_("Unpublish the selected articles"))
    def unpublish_articles(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(
            request,
            ngettext("%(count)d article unpublished.", "%(count)d articles unpublished.", updated)
            % {"count": updated},
        )
