from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Rental, RentalItem


class RentalItemInline(admin.TabularInline):
    model = RentalItem
    extra = 0
    fields = (
        "equipment",
        "quantity",
        "unit_price",
        "line_total",
        "condition_out",
        "condition_in",
        "damage_reported",
        "damage_type",
        "damage_charge",
        "returned_at",
    )
    readonly_fields = ("line_total",)
    raw_id_fields = ("equipment",)


@admin.register(Rental)
class RentalAdmin(admin.ModelAdmin):
    list_display = (
        "rental_code",
        "customer",
        "status",
        "period_type",
        "start_at",
        "expected_return_at",
        "returned_at",
        "total_amount",
        "payment_status",
    )
    list_filter = (
        "status",
        "period_type",
        "payment_status",
        "deposit_status",
        "id_document_held",
        "start_at",
    )
    search_fields = ("rental_code", "customer__first_name", "customer__last_name", "notes")
    date_hierarchy = "start_at"
    ordering = ("-start_at",)
    inlines = [RentalItemInline]
    readonly_fields = (
        "rental_code",
        "public_id",
        "subtotal",
        "damage_fee",
        "total_amount",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("customer", "student", "booking", "checked_out_by", "checked_in_by")
    fieldsets = (
        (None, {"fields": ("rental_code", "public_id", "status", "period_type")}),
        (_("Who"), {"fields": ("customer", "student", "booking")}),
        (_("When"), {"fields": ("start_at", "expected_return_at", "returned_at")}),
        (
            _("Deposit"),
            {"fields": ("deposit_amount", "deposit_returned", "deposit_status", "id_document_held")},
        ),
        (
            _("Money"),
            {
                "fields": (
                    "subtotal",
                    "discount_amount",
                    "late_fee",
                    "damage_fee",
                    "total_amount",
                    "paid_amount",
                    "payment_status",
                )
            },
        ),
        (_("Counter"), {"fields": ("checked_out_by", "checked_in_by", "notes")}),
        (_("Record"), {"fields": ("created_at", "updated_at", "is_deleted")}),
    )

    @admin.display(description=_("Overdue"), boolean=True)
    def is_overdue(self, obj) -> bool:
        return obj.is_overdue


@admin.register(RentalItem)
class RentalItemAdmin(admin.ModelAdmin):
    list_display = (
        "rental",
        "equipment",
        "quantity",
        "line_total",
        "condition_out",
        "condition_in",
        "damage_reported",
        "returned_at",
    )
    list_filter = ("damage_reported", "damage_type", "condition_in", "returned_at")
    search_fields = ("rental__rental_code",)
    raw_id_fields = ("rental", "equipment")
    readonly_fields = ("line_total", "public_id", "created_at", "updated_at")
