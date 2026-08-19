"""REST API for customers."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.models import Tag

from . import selectors, services
from .models import Customer, normalise_phone


class TagSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "slug", "color"]


class CustomerSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    is_minor = serializers.BooleanField(read_only=True)
    has_valid_waiver = serializers.SerializerMethodField()
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    tags = TagSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "public_id",
            "customer_code",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "photo",
            "birth_date",
            "age",
            "is_minor",
            "gender",
            "nationality",
            "preferred_language",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relation",
            "source",
            "source_label",
            "tags",
            "marketing_consent",
            "marketing_consent_at",
            "is_active",
            "first_visit_date",
            "last_visit_date",
            "lifetime_value",
            "total_bookings",
            "has_valid_waiver",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "customer_code",
            "marketing_consent_at",
            "first_visit_date",
            "last_visit_date",
            "lifetime_value",
            "total_bookings",
            "created_at",
            "updated_at",
        ]

    def get_has_valid_waiver(self, obj) -> bool:
        return obj.has_valid_waiver()


class CustomerWriteSerializer(serializers.ModelSerializer):
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(), many=True, required=False, write_only=True
    )
    allow_duplicate = serializers.BooleanField(required=False, write_only=True, default=False)

    class Meta:
        model = Customer
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "photo",
            "birth_date",
            "gender",
            "nationality",
            "preferred_language",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relation",
            "source",
            "marketing_consent",
            "is_active",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "notes",
            "tag_ids",
            "allow_duplicate",
        ]

    def validate_phone(self, value):
        return normalise_phone(value)

    def validate_email(self, value):
        return (value or "").strip().lower()

    def create(self, validated_data):
        tags = validated_data.pop("tag_ids", [])
        allow_duplicate = validated_data.pop("allow_duplicate", False)
        request = self.context.get("request")
        try:
            return services.create_customer(
                actor=getattr(request, "user", None),
                request=request,
                tags=tags,
                allow_duplicate=allow_duplicate,
                **validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", exc.messages)) from exc

    def update(self, instance, validated_data):
        tags = validated_data.pop("tag_ids", None)
        validated_data.pop("allow_duplicate", None)
        request = self.context.get("request")
        try:
            return services.update_customer(
                instance,
                actor=getattr(request, "user", None),
                request=request,
                tags=tags,
                **validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", exc.messages)) from exc


class CustomerViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD plus the operational actions the front desk needs."""

    capability_prefix = "customers"
    capability_overrides = {
        "duplicates": "customers.manage",
        "merge": "customers.manage",
        "recalculate": "customers.change",
        "consent": "customers.change",
    }
    queryset = (
        Customer.objects.select_related("user")
        .prefetch_related("tags")
        .order_by("last_name", "first_name")
    )
    filterset_fields = ["is_active", "source", "preferred_language", "marketing_consent"]
    search_fields = ["customer_code", "first_name", "last_name", "email", "phone"]
    ordering_fields = ["last_name", "created_at", "lifetime_value", "last_visit_date"]
    ordering = ["last_name", "first_name"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return CustomerWriteSerializer
        return CustomerSerializer

    def perform_destroy(self, instance):
        """Archive rather than destroy: bookings and invoices still point here."""
        services.deactivate_customer(
            instance, actor=self.request.user, request=self.request
        )
        instance.delete()  # soft delete

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except DjangoValidationError as exc:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "; ".join(exc.messages),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def duplicates(self, request):
        """Groups of customer records that look like the same person."""
        groups = services.find_duplicates()
        return Response(
            [
                {
                    "reason": str(group["reason"]),
                    "value": group["value"],
                    "customers": CustomerSerializer(
                        group["customers"], many=True, context=self.get_serializer_context()
                    ).data,
                }
                for group in groups
            ]
        )

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merge ``duplicate_id`` into this customer."""
        primary = self.get_object()
        duplicate_id = request.data.get("duplicate_id")
        duplicate = Customer.objects.filter(pk=duplicate_id).first()
        if duplicate is None:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "duplicate_id does not match a customer.",
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            services.merge_customers(
                primary, duplicate, actor=request.user, request=request
            )
        except DjangoValidationError as exc:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "; ".join(exc.messages),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        primary.refresh_from_db()
        return Response(CustomerSerializer(primary, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        """Rebuild lifetime value and visit dates from finance and bookings."""
        customer = self.get_object()
        services.recalculate_lifetime_value(customer)
        return Response(CustomerSerializer(customer, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def consent(self, request, pk=None):
        """Record a marketing opt-in (``{"granted": true}``) or opt-out."""
        customer = self.get_object()
        granted = bool(request.data.get("granted"))
        services.set_marketing_consent(
            customer, granted, actor=request.user, request=request
        )
        return Response(CustomerSerializer(customer, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        """Everything the detail screen shows, in one call."""
        customer = self.get_object()
        stats = selectors.booking_stats(customer)
        return Response(
            {
                "customer": CustomerSerializer(
                    customer, context=self.get_serializer_context()
                ).data,
                "bookings_total": stats["total"],
                "bookings_active": stats["active"],
                "paid_total": selectors.paid_total(customer),
                "open_balance": selectors.open_balance(customer),
                "has_valid_waiver": customer.has_valid_waiver(),
                "documents": len(selectors.customer_documents(customer)),
            }
        )


ROUTES = [("customers", CustomerViewSet, "customer")]
