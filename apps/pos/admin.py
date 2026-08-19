from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Product, ProductCategory, Sale, SaleItem, StockMovement


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")
    autocomplete_fields = ("parent",)
    prepopulated_fields = {"code": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "name",
        "category",
        "sale_price",
        "cost_price",
        "margin_display",
        "stock_quantity",
        "low_stock_threshold",
        "is_active",
    )
    list_filter = ("is_active", "track_stock", "unit", "category")
    search_fields = ("name", "sku", "barcode", "supplier", "description")
    ordering = ("sort_order", "name")
    autocomplete_fields = ("category",)
    # Stock is owned by the ledger; editing it here would desynchronise it.
    readonly_fields = ("public_id", "stock_quantity", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("sku", "barcode", "name", "description", "category", "photo")}),
        (_("Pricing"), {"fields": ("cost_price", "sale_price", "tax_rate")}),
        (
            _("Stock"),
            {
                "fields": ("track_stock", "unit", "stock_quantity", "low_stock_threshold"),
                "description": _(
                    "The stock figure is derived from the stock ledger. Correct it "
                    "from Point of Sale → Stock, never here."
                ),
            },
        ),
        (_("Shop"), {"fields": ("supplier", "is_active", "sort_order")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return Product.all_objects.select_related("category")

    @admin.display(description=_("margin"))
    def margin_display(self, obj) -> str:
        return f"{obj.margin_amount} ({obj.margin_percent}%)"


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    fields = ("product", "quantity", "unit_price", "discount_amount", "tax_amount", "line_total")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        # Receipts are written by the till, never typed into the admin.
        return False


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    fields = ("product", "movement_type", "quantity", "balance_after", "reference")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "sale_number",
        "sold_at",
        "customer",
        "cashier",
        "total_amount",
        "payment_method",
        "status",
    )
    list_filter = ("status", "payment_method", "sold_at")
    search_fields = ("sale_number", "note", "customer__first_name", "customer__last_name")
    date_hierarchy = "sold_at"
    ordering = ("-sold_at",)
    autocomplete_fields = ("customer", "cashier")
    readonly_fields = (
        "public_id",
        "sale_number",
        "subtotal",
        "discount_amount",
        "tax_amount",
        "total_amount",
        "paid_amount",
        "change_given",
        "voided_at",
        "created_at",
        "updated_at",
    )
    inlines = [SaleItemInline, StockMovementInline]

    def get_queryset(self, request):
        return Sale.all_objects.select_related("customer", "cashier")

    def has_delete_permission(self, request, obj=None) -> bool:
        """A receipt is never deleted — void it instead."""
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "movement_type",
        "quantity",
        "balance_after",
        "reference",
        "created_by",
    )
    list_filter = ("movement_type", "created_at")
    search_fields = ("product__name", "product__sku", "reference", "note")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    autocomplete_fields = ("product", "sale", "created_by")
    readonly_fields = ("balance_after",)

    def has_add_permission(self, request) -> bool:
        """Movements carry a running balance, so they are written by the
        service layer only — Point of Sale → Stock, or the REST action."""
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """The ledger is append-only: correct it with another movement."""
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
