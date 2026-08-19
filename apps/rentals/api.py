"""REST API for the hire counter.

Write operations go through :mod:`apps.rentals.services` so an API client can
never bypass an availability check or mis-price a hire.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import OWN, OwnerScopedQuerySetMixin
from apps.core.enums import RentalPeriod
from apps.core.utils import parse_date_range

from . import selectors, services
from .models import Rental, RentalItem

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class RentalItemSerializer(serializers.ModelSerializer):
    equipment_label = serializers.SerializerMethodField()
    is_returned = serializers.BooleanField(read_only=True)

    class Meta:
        model = RentalItem
        fields = [
            "id",
            "public_id",
            "equipment",
            "equipment_label",
            "unit_price",
            "quantity",
            "line_total",
            "condition_out",
            "condition_in",
            "damage_reported",
            "damage_type",
            "damage_notes",
            "damage_charge",
            "returned_at",
            "is_returned",
        ]
        read_only_fields = ["id", "public_id", "line_total", "returned_at"]

    def get_equipment_label(self, obj) -> str:
        return services.equipment_label(obj.equipment)


class RentalSerializer(serializers.ModelSerializer):
    items = RentalItemSerializer(many=True, read_only=True)
    customer_label = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    duration_hours = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    hours_overdue = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Rental
        fields = [
            "id",
            "public_id",
            "rental_code",
            "customer",
            "customer_label",
            "student",
            "booking",
            "status",
            "status_label",
            "period_type",
            "start_at",
            "expected_return_at",
            "returned_at",
            "deposit_amount",
            "deposit_returned",
            "deposit_status",
            "subtotal",
            "discount_amount",
            "late_fee",
            "damage_fee",
            "total_amount",
            "paid_amount",
            "payment_status",
            "balance_due",
            "duration_hours",
            "hours_overdue",
            "is_overdue",
            "item_count",
            "id_document_held",
            "notes",
            "items",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "rental_code",
            "status",
            "returned_at",
            "subtotal",
            "late_fee",
            "damage_fee",
            "total_amount",
            "deposit_returned",
            "deposit_status",
            "payment_status",
            "created_at",
        ]

    def get_customer_label(self, obj) -> str:
        return str(obj.customer)


class RentalLineInputSerializer(serializers.Serializer):
    equipment = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class RentalCreateSerializer(serializers.Serializer):
    """Check-out payload — mirrors the counter screen."""

    customer = serializers.IntegerField()
    student = serializers.IntegerField(required=False, allow_null=True)
    booking = serializers.IntegerField(required=False, allow_null=True)
    items = RentalLineInputSerializer(many=True)
    period_type = serializers.ChoiceField(choices=RentalPeriod.choices)
    start_at = serializers.DateTimeField(required=False)
    expected_return_at = serializers.DateTimeField()
    deposit_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=ZERO
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=ZERO
    )
    paid_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=ZERO
    )
    id_document_held = serializers.BooleanField(required=False, default=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value


class RentalReturnItemInputSerializer(serializers.Serializer):
    item = serializers.IntegerField()
    condition = serializers.CharField(required=False, allow_blank=True, default="")
    damage_type = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    charge = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=ZERO
    )


class RentalReturnSerializer(serializers.Serializer):
    items = RentalReturnItemInputSerializer(many=True, required=False)


class RentalExtendSerializer(serializers.Serializer):
    expected_return_at = serializers.DateTimeField()


class RentalCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=True, default="")


class RentalPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.CharField(required=False, allow_blank=True, default="")


def _error(exc: DjangoValidationError) -> Response:
    return Response(
        {
            "error": {
                "type": "validation_error",
                "message": " ".join(str(m) for m in exc.messages),
                "detail": {"messages": [str(m) for m in exc.messages]},
            }
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class RentalViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Hire contracts."""

    capability_prefix = "rentals"
    capability_overrides = {
        "check_in": "rentals.change",
        "extend": "rentals.change",
        "cancel": "rentals.change",
        "payment": "rentals.change",
        "quick_return": "rentals.change",
        "overdue": "rentals.view",
        # ``revenue`` is a school-wide takings aggregate, not a hire record.
        "revenue": "finance.revenue",
    }
    external_access = OWN
    owner_lookups = ("customer__user", "student__customer__user")
    queryset = (
        Rental.objects.select_related("customer", "student", "booking")
        .prefetch_related("items__equipment")
        .order_by("-start_at", "-id")
    )
    serializer_class = RentalSerializer
    filterset_fields = ["status", "period_type", "payment_status", "deposit_status", "customer"]
    search_fields = ["rental_code", "customer__first_name", "customer__last_name"]
    ordering_fields = ["start_at", "expected_return_at", "total_amount", "created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return RentalCreateSerializer
        return RentalSerializer

    def create(self, request, *args, **kwargs):
        serializer = RentalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        customer_model = services.customer_model()
        equipment_model = services.equipment_model()
        try:
            customer = customer_model.objects.get(pk=data["customer"])
        except customer_model.DoesNotExist:
            return Response(
                {"error": {"type": "validation_error", "message": "Unknown customer.", "detail": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assets = {
            obj.pk: obj
            for obj in equipment_model.objects.filter(
                pk__in=[line["equipment"] for line in data["items"]]
            )
        }
        missing = [line["equipment"] for line in data["items"] if line["equipment"] not in assets]
        if missing:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "Unknown equipment.",
                        "detail": {"equipment": missing},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        student = None
        if data.get("student"):
            student = (
                apps.get_model("students", "Student").objects.filter(pk=data["student"]).first()
            )
        booking = None
        if data.get("booking"):
            booking = (
                apps.get_model("bookings", "Booking").objects.filter(pk=data["booking"]).first()
            )

        try:
            rental = services.create_rental(
                customer=customer,
                items=[(assets[line["equipment"]], line["quantity"]) for line in data["items"]],
                period_type=data["period_type"],
                start_at=data.get("start_at") or timezone.now(),
                expected_return_at=data["expected_return_at"],
                student=student,
                booking=booking,
                deposit_amount=data.get("deposit_amount") or ZERO,
                discount_amount=data.get("discount_amount") or ZERO,
                paid_amount=data.get("paid_amount") or ZERO,
                id_document_held=data.get("id_document_held") or False,
                notes=data.get("notes") or "",
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return _error(exc)

        return Response(RentalSerializer(rental).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        """Check equipment back in and settle late fee, damage and deposit."""
        rental = self.get_object()
        serializer = RentalReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conditions = {
            line["item"]: (
                line.get("condition") or "",
                line.get("damage_type") or "",
                line.get("notes") or "",
                line.get("charge") or ZERO,
            )
            for line in serializer.validated_data.get("items", [])
        }
        try:
            rental = services.return_rental(rental, conditions, request.user, request=request)
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(RentalSerializer(rental).data)

    @action(detail=True, methods=["post"])
    def extend(self, request, pk=None):
        rental = self.get_object()
        serializer = RentalExtendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rental = services.extend_rental(
                rental,
                serializer.validated_data["expected_return_at"],
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(RentalSerializer(rental).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        rental = self.get_object()
        serializer = RentalCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rental = services.cancel_rental(
                rental,
                user=request.user,
                reason=serializer.validated_data.get("reason", ""),
                request=request,
            )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(RentalSerializer(rental).data)

    @action(detail=True, methods=["post"])
    def payment(self, request, pk=None):
        rental = self.get_object()
        serializer = RentalPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rental = services.register_payment(
                rental,
                serializer.validated_data["amount"],
                user=request.user,
                method=serializer.validated_data.get("method", ""),
                request=request,
            )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(RentalSerializer(rental).data)

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        """Everything past its due-back time, worst first."""
        # Built outside ``get_queryset()``, so scope it explicitly.
        queryset = self.scope(selectors.overdue_rentals()).order_by("expected_return_at")
        page = self.paginate_queryset(queryset)
        serializer = RentalSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def revenue(self, request):
        """Hire revenue for the standard date-range vocabulary (``?range=30``)."""
        start, end, label = parse_date_range(request)
        return Response(
            {
                "range": label,
                "start": start,
                "end": end,
                "revenue": services.rental_revenue(start, end),
                "stats": selectors.counter_stats(),
            }
        )


class RentalItemViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Individual hired assets — used by the equipment module's history tab."""

    capability_prefix = "rentals"
    external_access = OWN
    owner_lookups = ("rental__customer__user", "rental__student__customer__user")
    queryset = RentalItem.objects.select_related("equipment", "rental", "rental__customer")
    serializer_class = RentalItemSerializer
    filterset_fields = ["equipment", "rental", "damage_reported", "condition_in"]
    ordering_fields = ["returned_at", "created_at"]
    ordering = ["-created_at"]


ROUTES = [
    ("rentals", RentalViewSet, "rental"),
    ("rental-items", RentalItemViewSet, "rental-item"),
]
