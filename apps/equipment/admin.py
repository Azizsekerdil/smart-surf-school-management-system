from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Equipment, EquipmentCategory, EquipmentPhoto


class EquipmentPhotoInline(admin.TabularInline):
    model = EquipmentPhoto
    extra = 0
    fields = ("image", "caption", "is_primary", "taken_at")


@admin.register(EquipmentCategory)
class EquipmentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("code", "name")
    ordering = ("sort_order", "name")
    prepopulated_fields = {"code": ("name",)}


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        "asset_code",
        "name",
        "category",
        "size_label",
        "status",
        "condition",
        "is_rentable",
        "next_maintenance_date",
    )
    list_filter = ("status", "condition", "category", "is_rentable", "is_lesson_stock")
    search_fields = ("asset_code", "name", "brand", "model", "serial_number")
    ordering = ("asset_code",)
    date_hierarchy = "created_at"
    autocomplete_fields = ("category",)
    readonly_fields = (
        "public_id",
        "total_rentals",
        "total_rental_hours",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )
    inlines = [EquipmentPhotoInline]
    fieldsets = (
        (None, {"fields": ("asset_code", "category", "name", "brand", "model", "serial_number")}),
        (
            _("Dimensions"),
            {
                "fields": (
                    "size_label",
                    "length_cm",
                    "width_cm",
                    "thickness_cm",
                    "volume_litres",
                    "wetsuit_thickness",
                )
            },
        ),
        (
            _("Suitability"),
            {
                "fields": (
                    "suitable_min_level",
                    "suitable_max_level",
                    "min_rider_weight_kg",
                    "max_rider_weight_kg",
                )
            },
        ),
        (_("Purchase"), {"fields": ("purchase_date", "purchase_price", "current_value", "supplier")}),
        (_("State"), {"fields": ("status", "condition", "storage_location")}),
        (
            _("Rental"),
            {
                "fields": (
                    "is_rentable",
                    "is_lesson_stock",
                    "rental_price_hourly",
                    "rental_price_daily",
                    "rental_price_weekly",
                    "deposit_amount",
                )
            },
        ),
        (_("Usage"), {"fields": ("total_rentals", "total_rental_hours")}),
        (_("Service"), {"fields": ("last_maintenance_date", "next_maintenance_date")}),
        (_("Lifecycle"), {"fields": ("notes", "retired_at", "retired_reason", "is_deleted")}),
        (
            _("Record"),
            {
                "classes": ("collapse",),
                "fields": ("public_id", "created_at", "updated_at", "created_by", "updated_by"),
            },
        ),
    )

    def get_queryset(self, request):
        # Archived items must stay reachable from the admin.
        return Equipment.all_objects.select_related("category")


@admin.register(EquipmentPhoto)
class EquipmentPhotoAdmin(admin.ModelAdmin):
    list_display = ("equipment", "caption", "is_primary", "taken_at")
    list_filter = ("is_primary",)
    search_fields = ("equipment__asset_code", "equipment__name", "caption")
