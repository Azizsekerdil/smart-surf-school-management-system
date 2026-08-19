"""REST API for the finance module.

Money is never created by a plain serializer ``save()``: payments, refunds and
package sales all go through :mod:`apps.finance.services`, so the API and the
HTML screens obey exactly the same rules, write the same audit entries and
update the same balances.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import DENY, OWN, SHARED, OwnerScopedQuerySetMixin
from apps.core.utils import parse_date_range

from . import selectors, services
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

ZERO = Decimal("0.00")


def _error(message, detail=None, code="validation_error"):
    return Response(
        {"error": {"type": code, "message": str(message), "detail": detail or {}}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _range_from(request):
    start, end, label = parse_date_range(request)
    return start, end, label


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "description",
            "quantity",
            "unit_price",
            "discount_amount",
            "line_total",
            "sort_order",
        ]
        read_only_fields = ["id", "line_total"]


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "public_id",
            "invoice_number",
            "customer",
            "customer_name",
            "booking",
            "rental",
            "issue_date",
            "due_date",
            "status",
            "status_label",
            "subtotal",
            "discount_amount",
            "tax_rate",
            "tax_amount",
            "total_amount",
            "paid_amount",
            "balance_due",
            "is_overdue",
            "days_overdue",
            "currency",
            "notes",
            "terms",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "invoice_number",
            "subtotal",
            "tax_amount",
            "total_amount",
            "paid_amount",
            "status",
            "currency",
            "created_at",
            "updated_at",
        ]


class InvoiceWriteSerializer(serializers.ModelSerializer):
    """Create an invoice together with its lines in one request."""

    lines = InvoiceLineSerializer(many=True, write_only=True)

    class Meta:
        model = Invoice
        fields = [
            "customer",
            "booking",
            "rental",
            "issue_date",
            "due_date",
            "discount_amount",
            "tax_rate",
            "notes",
            "terms",
            "lines",
        ]

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError(_("An invoice needs at least one line."))
        return value

    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        request = self.context.get("request")
        try:
            return services.create_invoice(
                validated_data["customer"],
                [dict(line) for line in lines],
                booking=validated_data.get("booking"),
                rental=validated_data.get("rental"),
                issue_date=validated_data.get("issue_date"),
                due_date=validated_data.get("due_date"),
                discount_amount=validated_data.get("discount_amount") or ZERO,
                tax_rate=validated_data.get("tax_rate"),
                notes=validated_data.get("notes", ""),
                terms=validated_data.get("terms", ""),
                user=getattr(request, "user", None),
                request=request,
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    refundable_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    refunded_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "public_id",
            "payment_code",
            "customer",
            "customer_name",
            "invoice",
            "booking",
            "rental",
            "amount",
            "method",
            "method_label",
            "category",
            "category_label",
            "status",
            "paid_at",
            "reference",
            "received_by",
            "is_refund",
            "refunded_payment",
            "refund_reason",
            "refunded_amount",
            "refundable_amount",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "payment_code",
            "received_by",
            "is_refund",
            "refunded_payment",
            "refund_reason",
            "created_at",
        ]

    def create(self, validated_data):
        request = self.context.get("request")
        try:
            return services.record_payment(
                validated_data["customer"],
                validated_data["amount"],
                method=validated_data.get("method"),
                category=validated_data.get("category"),
                invoice=validated_data.get("invoice"),
                booking=validated_data.get("booking"),
                rental=validated_data.get("rental"),
                paid_at=validated_data.get("paid_at"),
                reference=validated_data.get("reference", ""),
                notes=validated_data.get("notes", ""),
                user=getattr(request, "user", None),
                request=request,
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error


class RefundRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField(max_length=1000)


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ["id", "code", "name", "is_active", "sort_order"]
        read_only_fields = ["id"]


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "public_id",
            "expense_code",
            "category",
            "category_name",
            "description",
            "amount",
            "tax_amount",
            "total_amount",
            "spent_on",
            "paid_by",
            "supplier",
            "invoice_reference",
            "receipt",
            "is_recurring",
            "recurrence_months",
            "equipment",
            "created_at",
        ]
        read_only_fields = ["id", "public_id", "expense_code", "created_at"]


class CommissionRecordSerializer(serializers.ModelSerializer):
    instructor_name = serializers.CharField(source="instructor.full_name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = CommissionRecord
        fields = [
            "id",
            "public_id",
            "instructor",
            "instructor_name",
            "lesson",
            "period_start",
            "period_end",
            "base_amount",
            "commission_percent",
            "commission_amount",
            "status",
            "status_label",
            "paid_at",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "commission_amount",
            "status",
            "paid_at",
            "created_at",
        ]


class PricePackageSerializer(serializers.ModelSerializer):
    price_per_lesson = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    saving_vs_single = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = PricePackage
        fields = [
            "id",
            "public_id",
            "name",
            "code",
            "description",
            "lesson_type",
            "lesson_count",
            "price",
            "price_per_lesson",
            "saving_vs_single",
            "validity_days",
            "is_active",
            "sort_order",
        ]
        read_only_fields = ["id", "public_id"]


class CustomerPackageSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    package_name = serializers.CharField(source="package.name", read_only=True)
    lessons_remaining = serializers.IntegerField(read_only=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = CustomerPackage
        fields = [
            "id",
            "public_id",
            "customer",
            "customer_name",
            "package",
            "package_name",
            "purchased_on",
            "expires_on",
            "lessons_total",
            "lessons_used",
            "lessons_remaining",
            "is_usable",
            "amount_paid",
            "status",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "lessons_used",
            "amount_paid",
            "status",
            "created_at",
        ]


class SellPackageRequestSerializer(serializers.Serializer):
    customer = serializers.IntegerField()
    package = serializers.IntegerField()
    payment_method = serializers.CharField(max_length=12)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class InvoiceViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Invoices: what customers owe the school.

    ``finance.view`` is granted to Role.CUSTOMER so the self-service portal can
    show a customer their own bill. The ownership rule below is what stops it
    from showing them everybody else's.
    """

    capability_prefix = "finance"
    external_access = OWN
    owner_lookups = ("customer__user",)
    capability_overrides = {
        "issue": "finance.change",
        "cancel": "finance.change",
        "overdue": "finance.view",
    }
    queryset = selectors.invoice_queryset().prefetch_related("lines")
    serializer_class = InvoiceSerializer
    filterset_fields = ["status", "customer", "currency"]
    search_fields = ["invoice_number", "customer__first_name", "customer__last_name"]
    ordering_fields = ["issue_date", "due_date", "total_amount", "created_at"]
    ordering = ["-issue_date", "-id"]

    def get_serializer_class(self):
        if self.action == "create":
            return InvoiceWriteSerializer
        return InvoiceSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save()
        return Response(
            InvoiceSerializer(invoice, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """Turn a draft invoice into a receivable."""
        invoice = self.get_object()
        try:
            services.issue_invoice(invoice, user=request.user, request=request)
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(InvoiceSerializer(invoice, context=self.get_serializer_context()).data)

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Void an unpaid invoice."""
        invoice = self.get_object()
        reason = str(request.data.get("reason", ""))[:500]
        try:
            services.cancel_invoice(invoice, reason, user=request.user, request=request)
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(InvoiceSerializer(invoice, context=self.get_serializer_context()).data)

    @extend_schema(responses=InvoiceSerializer(many=True))
    @action(detail=False, methods=["get"])
    def overdue(self, request):
        """Invoices past their due date with money still outstanding."""
        # This action builds its own queryset, so it must apply the ownership
        # rule by hand — ``get_queryset()`` is never called on this path.
        queryset = self.scope(services.overdue_invoices())
        page = self.paginate_queryset(queryset)
        serializer = InvoiceSerializer(
            page if page is not None else queryset,
            many=True,
            context=self.get_serializer_context(),
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class PaymentViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """The money ledger. Rows are append-only: no updates, no deletes."""

    capability_prefix = "finance"
    # ``summary`` returns school-wide revenue, cost and margin aggregates, so it
    # is gated on finance.revenue rather than finance.view. Roles that take
    # money at a counter (reception, rental staff) hold finance.view but not
    # finance.revenue, and therefore cannot read the school's takings.
    capability_overrides = {"refund": "finance.refund", "summary": "finance.revenue"}
    external_access = OWN
    owner_lookups = ("customer__user",)
    http_method_names = ["get", "post", "head", "options"]
    queryset = selectors.payment_queryset()
    serializer_class = PaymentSerializer
    filterset_fields = ["category", "method", "status", "is_refund", "customer", "invoice"]
    search_fields = ["payment_code", "reference", "customer__first_name", "customer__last_name"]
    ordering_fields = ["paid_at", "amount", "created_at"]
    ordering = ["-paid_at", "-id"]

    @extend_schema(request=RefundRequestSerializer, responses=PaymentSerializer)
    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Write the negative counterpart that reverses this payment."""
        payment = self.get_object()
        form = RefundRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            refund = services.refund_payment(
                payment,
                form.validated_data["amount"],
                form.validated_data["reason"],
                user=request.user,
                request=request,
            )
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            PaymentSerializer(refund, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("range", str, description="today / 7 / 30 / 90 / 365 / all / custom"),
            OpenApiParameter("start", str, description="ISO date, with range=custom."),
            OpenApiParameter("end", str, description="ISO date, with range=custom."),
        ]
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Revenue, costs, profit and receivables for the selected period."""
        start, end, label = _range_from(request)
        summary = services.financial_summary(start, end)
        return Response(
            {
                "period": {
                    "label": label,
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                },
                "revenue": summary["revenue"],
                "expenses": summary["expenses"],
                "gross_profit": summary["gross_profit"],
                "refunds": summary["refunds"],
                "margin_percent": summary["margin_percent"],
                "revenue_by_category": summary["revenue_by_category"],
                "expenses_by_category": summary["expenses_by_category"],
                "outstanding_receivables": summary["outstanding_receivables"],
                "commission_owed": summary["commission_owed"],
                "package_liability": summary["package_liability"],
            }
        )


class ExpenseCategoryViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Buckets the school files its outgoings under."""

    capability_prefix = "finance"
    # The cost structure of the school is not customer-facing data.
    external_access = DENY
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering = ["sort_order", "name"]


class ExpenseViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Money the school has spent."""

    capability_prefix = "finance"
    external_access = DENY
    queryset = selectors.expense_queryset()
    serializer_class = ExpenseSerializer
    filterset_fields = ["category", "is_recurring", "equipment"]
    search_fields = ["expense_code", "description", "supplier", "invoice_reference"]
    ordering_fields = ["spent_on", "amount", "created_at"]
    ordering = ["-spent_on", "-id"]

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            paid_by=serializer.validated_data.get("paid_by") or self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class CommissionRecordViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """What the school owes its instructors."""

    capability_prefix = "finance"
    # Instructor pay is staff data.
    external_access = DENY
    capability_overrides = {
        "approve": "finance.approve",
        "pay": "finance.change",
        "calculate": "finance.add",
    }
    queryset = selectors.commission_queryset()
    serializer_class = CommissionRecordSerializer
    filterset_fields = ["status", "instructor", "lesson"]
    search_fields = ["instructor__instructor_code", "instructor__user__last_name"]
    ordering_fields = ["period_end", "commission_amount", "created_at"]
    ordering = ["-period_end", "-id"]

    def perform_create(self, serializer):
        record = serializer.save(
            created_by=self.request.user, updated_by=self.request.user
        )
        record.compute_amount()
        record.save(update_fields=["commission_amount", "updated_at"])

    @extend_schema(request=None, responses=CommissionRecordSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Sign off a pending commission row."""
        record = self.get_object()
        try:
            services.approve_commission(record, user=request.user, request=request)
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            CommissionRecordSerializer(record, context=self.get_serializer_context()).data
        )

    @extend_schema(request=None, responses=CommissionRecordSerializer)
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """Mark an approved commission as paid out."""
        record = self.get_object()
        try:
            services.pay_commission(record, user=request.user, request=request)
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            CommissionRecordSerializer(record, context=self.get_serializer_context()).data
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("instructor", int, required=True),
            OpenApiParameter("start", str, description="ISO date."),
            OpenApiParameter("end", str, description="ISO date."),
        ],
        request=None,
        responses=CommissionRecordSerializer(many=True),
    )
    @action(detail=False, methods=["post"])
    def calculate(self, request):
        """Create the commission rows an instructor earned in a period."""
        from apps.instructors.models import Instructor

        raw_id = request.data.get("instructor") or request.query_params.get("instructor")
        try:
            instructor = Instructor.objects.get(pk=int(raw_id))
        except (TypeError, ValueError, Instructor.DoesNotExist):
            return _error(_("Provide a valid instructor id."), code="not_found")

        start, end, _label = _range_from(request)
        try:
            records = services.calculate_commission(
                instructor, start, end, user=request.user, request=request
            )
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            CommissionRecordSerializer(
                records, many=True, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )


class PricePackageViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Lesson bundles the school sells."""

    capability_prefix = "finance"
    # A price list is catalogue data — the same rows every customer is quoted.
    external_access = SHARED
    queryset = selectors.package_queryset()
    serializer_class = PricePackageSerializer
    filterset_fields = ["is_active", "lesson_type"]
    search_fields = ["code", "name", "description"]
    ordering_fields = ["sort_order", "price", "lesson_count"]
    ordering = ["sort_order", "name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class CustomerPackageViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Package cards customers hold. Created through ``sell``, never by hand."""

    capability_prefix = "finance"
    external_access = OWN
    owner_lookups = ("customer__user",)
    capability_overrides = {"sell": "finance.add", "use": "finance.change"}
    queryset = selectors.customer_package_queryset()
    serializer_class = CustomerPackageSerializer
    filterset_fields = ["status", "customer", "package"]
    search_fields = ["customer__first_name", "customer__last_name", "package__code"]
    ordering_fields = ["purchased_on", "expires_on"]
    ordering = ["-purchased_on", "-id"]

    @extend_schema(request=SellPackageRequestSerializer, responses=CustomerPackageSerializer)
    @action(detail=False, methods=["post"])
    def sell(self, request):
        """Sell a package and take the money in one transaction."""
        from apps.customers.models import Customer

        form = SellPackageRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            customer = Customer.objects.get(pk=form.validated_data["customer"])
            package = PricePackage.objects.get(pk=form.validated_data["package"])
        except (Customer.DoesNotExist, PricePackage.DoesNotExist):
            return _error(_("Unknown customer or package."), code="not_found")

        try:
            customer_package, _payment = services.sell_package(
                customer,
                package,
                form.validated_data["payment_method"],
                request.user,
                reference=form.validated_data.get("reference", ""),
                request=request,
            )
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            CustomerPackageSerializer(
                customer_package, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=None, responses=CustomerPackageSerializer)
    @action(detail=True, methods=["post"], url_path="use")
    def use(self, request, pk=None):
        """Take one lesson off this package against a booking."""
        from apps.bookings.models import Booking

        customer_package = self.get_object()
        try:
            booking = Booking.objects.get(pk=int(request.data.get("booking")))
        except (TypeError, ValueError, Booking.DoesNotExist):
            return _error(_("Provide a valid booking id."), code="not_found")

        try:
            services.use_package_lesson(
                customer_package, booking, user=request.user, request=request
            )
        except DjangoValidationError as error:
            return _error("; ".join(error.messages))
        return Response(
            CustomerPackageSerializer(
                customer_package, context=self.get_serializer_context()
            ).data
        )


ROUTES = [
    ("finance/invoices", InvoiceViewSet, "finance-invoice"),
    ("finance/payments", PaymentViewSet, "finance-payment"),
    ("finance/expense-categories", ExpenseCategoryViewSet, "finance-expense-category"),
    ("finance/expenses", ExpenseViewSet, "finance-expense"),
    ("finance/commissions", CommissionRecordViewSet, "finance-commission"),
    ("finance/packages", PricePackageViewSet, "finance-package"),
    ("finance/customer-packages", CustomerPackageViewSet, "finance-customer-package"),
]
