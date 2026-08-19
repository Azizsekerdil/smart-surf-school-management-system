from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Customer, CustomerTag


class CustomerTagInline(admin.TabularInline):
    model = CustomerTag
    extra = 0
    readonly_fields = ("added_at",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "customer_code",
        "full_name",
        "email",
        "phone",
        "source",
        "total_bookings",
        "lifetime_value",
        "is_active",
    )
    list_filter = ("is_active", "source", "preferred_language", "marketing_consent", "is_deleted")
    search_fields = ("customer_code", "first_name", "last_name", "email", "phone")
    ordering = ("last_name", "first_name")
    date_hierarchy = "created_at"
    inlines = (CustomerTagInline,)
    autocomplete_fields = ("user",)
    readonly_fields = (
        "customer_code",
        "public_id",
        "marketing_consent_at",
        "lifetime_value",
        "total_bookings",
        "first_visit_date",
        "last_visit_date",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("customer_code", "public_id", "user", "is_active")}),
        (
            _("Identity"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                    "photo",
                    "birth_date",
                    "gender",
                    "nationality",
                    "preferred_language",
                )
            },
        ),
        (
            _("Emergency contact"),
            {
                "fields": (
                    "emergency_contact_name",
                    "emergency_contact_phone",
                    "emergency_contact_relation",
                )
            },
        ),
        (
            _("Address"),
            {
                "fields": (
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "postal_code",
                    "country",
                )
            },
        ),
        (
            _("Commercial"),
            {
                "fields": (
                    "source",
                    "marketing_consent",
                    "marketing_consent_at",
                    "first_visit_date",
                    "last_visit_date",
                    "lifetime_value",
                    "total_bookings",
                )
            },
        ),
        (_("Internal"), {"fields": ("notes", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return Customer.all_objects.all()

    @admin.display(description=_("name"), ordering="last_name")
    def full_name(self, obj: Customer) -> str:
        return obj.full_name


@admin.register(CustomerTag)
class CustomerTagAdmin(admin.ModelAdmin):
    list_display = ("customer", "tag", "added_at", "added_by")
    list_filter = ("tag",)
    search_fields = ("customer__customer_code", "customer__last_name", "tag__name")
    autocomplete_fields = ("customer",)
