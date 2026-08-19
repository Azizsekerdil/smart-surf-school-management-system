from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    Payment,
    PricePackage,
)


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ("sort_order", "description", "quantity", "unit_price", "discount_amount", "line_total")
    readonly_fields = ("line_total",)


class PaymentInline(admin.TabularInline):
    model = Payment
    fk_name = "invoice"
    extra = 0
    fields = ("payment_code", "paid_at", "amount", "method", "status", "is_refund")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        # Payments are only ever created through the service layer, so the
        # balances and audit trail cannot be bypassed from the admin.
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "customer",
        "issue_date",
        "due_date",
        "status",
        "total_amount",
        "paid_amount",
        "balance_due_display",
    )
    list_filter = ("status", "issue_date", "currency")
    search_fields = (
        "invoice_number",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
    )
    date_hierarchy = "issue_date"
    autocomplete_fields = ("customer",)
    readonly_fields = ("public_id", "invoice_number", "created_at", "updated_at")
    inlines = [InvoiceLineInline, PaymentInline]
    ordering = ("-issue_date", "-id")

    fieldsets = (
        (None, {"fields": ("invoice_number", "customer", "status", "currency")}),
        (_("Linked records"), {"fields": ("booking", "rental")}),
        (_("Dates"), {"fields": ("issue_date", "due_date")}),
        (
            _("Money"),
            {
                "fields": (
                    "subtotal",
                    "discount_amount",
                    "tax_rate",
                    "tax_amount",
                    "total_amount",
                    "paid_amount",
                )
            },
        ),
        (_("Text"), {"fields": ("notes", "terms")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return Invoice.all_objects.select_related("customer")

    @admin.display(description=_("balance"))
    def balance_due_display(self, obj) -> str:
        return f"{obj.balance_due:,.2f}"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "payment_code",
        "paid_at",
        "customer",
        "amount",
        "method",
        "category",
        "status",
        "is_refund",
    )
    list_filter = ("method", "category", "status", "is_refund", "paid_at")
    search_fields = (
        "payment_code",
        "reference",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
    )
    date_hierarchy = "paid_at"
    autocomplete_fields = ("customer",)
    readonly_fields = ("public_id", "payment_code", "created_at", "updated_at")
    ordering = ("-paid_at", "-id")

    def get_queryset(self, request):
        return Payment.all_objects.select_related("customer", "invoice", "received_by")


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("sort_order", "name")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "expense_code",
        "spent_on",
        "category",
        "description",
        "amount",
        "tax_amount",
        "supplier",
        "is_recurring",
    )
    list_filter = ("category", "is_recurring", "spent_on")
    search_fields = ("expense_code", "description", "supplier", "invoice_reference")
    date_hierarchy = "spent_on"
    readonly_fields = ("public_id", "expense_code", "created_at", "updated_at")
    ordering = ("-spent_on", "-id")

    def get_queryset(self, request):
        return Expense.all_objects.select_related("category", "paid_by", "equipment")


@admin.register(CommissionRecord)
class CommissionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "instructor",
        "period_start",
        "period_end",
        "base_amount",
        "commission_percent",
        "commission_amount",
        "status",
        "paid_at",
    )
    list_filter = ("status", "period_end")
    search_fields = (
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
    )
    readonly_fields = ("public_id", "created_at", "updated_at")
    ordering = ("-period_end", "-id")

    def get_queryset(self, request):
        return CommissionRecord.all_objects.select_related(
            "instructor", "instructor__user", "lesson"
        )


@admin.register(PricePackage)
class PricePackageAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "lesson_count",
        "price",
        "price_per_lesson_display",
        "validity_days",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "lesson_type")
    search_fields = ("code", "name", "description")
    readonly_fields = ("public_id", "created_at", "updated_at")
    ordering = ("sort_order", "name")

    def get_queryset(self, request):
        return PricePackage.all_objects.select_related("lesson_type")

    @admin.display(description=_("per lesson"))
    def price_per_lesson_display(self, obj) -> str:
        return f"{obj.price_per_lesson:,.2f}"


@admin.register(CustomerPackage)
class CustomerPackageAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "package",
        "purchased_on",
        "expires_on",
        "lessons_used",
        "lessons_total",
        "status",
        "amount_paid",
    )
    list_filter = ("status", "expires_on", "package")
    search_fields = (
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
        "package__code",
        "package__name",
    )
    autocomplete_fields = ("customer",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    ordering = ("-purchased_on", "-id")

    def get_queryset(self, request):
        return CustomerPackage.all_objects.select_related("customer", "package")
