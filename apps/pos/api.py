"""REST API for the shop.

Two things other modules genuinely need from here: what is on the shelf, and
what was sold. Both are exposed as read-shaped resources with the derived values
(margin, on-hand balance, contained tax) already computed, so nobody has to
re-implement the tax convention on a client.

Writes are deliberately narrow. Stock moves only through the ``adjust-stock``
action, sales are created only through ``checkout`` and cancelled only through
``void`` — all three go through :mod:`apps.pos.services`, so the API can never
produce a receipt the till could not have produced.
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
from apps.core.enums import PaymentMethod
from apps.core.utils import parse_date_range

from . import selectors, services
from .models import Product, ProductCategory, Sale, SaleItem, StockMovement

ZERO = Decimal("0.00")


def _error(message, detail=None, error_type: str = "validation_error"):
    return {
        "error": {
            "type": error_type,
            "message": str(message),
            "detail": detail or {},
        }
    }


def _from_validation_error(error: DjangoValidationError) -> dict:
    detail = getattr(error, "message_dict", None) or {}
    return _error("; ".join(error.messages), detail)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class ProductCategorySerializer(serializers.ModelSerializer):
    full_path = serializers.CharField(read_only=True)
    product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "code",
            "name",
            "parent",
            "full_path",
            "icon",
            "sort_order",
            "is_active",
            "product_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance or ProductCategory()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                getattr(error, "message_dict", None) or error.messages
            ) from error
        return attrs


class ProductSerializer(serializers.ModelSerializer):
    """Read representation, with every derived figure precomputed."""

    category_name = serializers.CharField(source="category.name", read_only=True)
    unit_label = serializers.CharField(source="get_unit_display", read_only=True)
    stock_balance = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    net_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    tax_component = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    margin_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    margin_percent = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    stock_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    is_out_of_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "public_id",
            "sku",
            "barcode",
            "name",
            "description",
            "category",
            "category_name",
            "cost_price",
            "sale_price",
            "net_price",
            "tax_rate",
            "tax_component",
            "margin_amount",
            "margin_percent",
            "stock_quantity",
            "stock_balance",
            "stock_value",
            "low_stock_threshold",
            "track_stock",
            "is_low_stock",
            "is_out_of_stock",
            "unit",
            "unit_label",
            "photo",
            "supplier",
            "is_active",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "stock_quantity", "created_at", "updated_at"]


class ProductWriteSerializer(serializers.ModelSerializer):
    """Write representation. ``stock_quantity`` is absent by design.

    Stock is owned by the ledger; a client that wants to change it calls
    ``/products/{id}/adjust-stock/``, which writes a movement somebody can audit.
    """

    opening_stock = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, min_value=ZERO, write_only=True
    )

    class Meta:
        model = Product
        fields = [
            "sku",
            "barcode",
            "name",
            "description",
            "category",
            "cost_price",
            "sale_price",
            "tax_rate",
            "unit",
            "track_stock",
            "low_stock_threshold",
            "supplier",
            "photo",
            "is_active",
            "sort_order",
            "opening_stock",
        ]

    def validate(self, attrs):
        instance = self.instance or Product()
        for field, value in attrs.items():
            if field == "opening_stock":
                continue
            setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                getattr(error, "message_dict", None) or error.messages
            ) from error
        return attrs


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    net_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "quantity",
            "unit_price",
            "discount_amount",
            "tax_amount",
            "line_total",
            "net_total",
        ]
        read_only_fields = fields


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    payment_method_label = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    customer_name = serializers.SerializerMethodField()
    cashier_name = serializers.SerializerMethodField()
    net_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id",
            "public_id",
            "sale_number",
            "sold_at",
            "customer",
            "customer_name",
            "cashier",
            "cashier_name",
            "subtotal",
            "discount_amount",
            "discount_percent",
            "tax_amount",
            "total_amount",
            "net_amount",
            "paid_amount",
            "change_given",
            "payment_method",
            "payment_method_label",
            "status",
            "status_label",
            "note",
            "voided_at",
            "void_reason",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_customer_name(self, obj) -> str:
        return str(obj.customer) if obj.customer_id else ""

    def get_cashier_name(self, obj) -> str:
        return obj.cashier.get_full_name() or obj.cashier.username if obj.cashier_id else ""


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    movement_type_label = serializers.CharField(
        source="get_movement_type_display", read_only=True
    )
    sale_number = serializers.CharField(source="sale.sale_number", read_only=True, default="")

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "movement_type",
            "movement_type_label",
            "quantity",
            "balance_after",
            "reference",
            "note",
            "sale",
            "sale_number",
            "created_by",
            "created_at",
        ]
        read_only_fields = fields


class StockAdjustmentSerializer(serializers.Serializer):
    """Signed delta plus a reason — exactly what the ledger records."""

    quantity = serializers.DecimalField(max_digits=8, decimal_places=2)
    reason = serializers.CharField(max_length=500)
    movement_type = serializers.ChoiceField(
        choices=StockMovement.MovementType.choices,
        default=StockMovement.MovementType.ADJUSTMENT,
    )
    reference = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_quantity(self, value):
        if value == ZERO:
            raise serializers.ValidationError(_("A movement of zero changes nothing."))
        return value


class CheckoutLineSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("0.01")
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=ZERO
    )


class CheckoutSerializer(serializers.Serializer):
    """A whole sale in one request, for an unattended or mobile till."""

    items = CheckoutLineSerializer(many=True)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    amount_tendered = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=ZERO
    )
    customer = serializers.IntegerField(required=False, allow_null=True)
    discount_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=ZERO, max_value=Decimal("100")
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=ZERO
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError(_("A sale needs at least one line."))
        return value


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)

    def validate_reason(self, value):
        if len((value or "").strip()) < 5:
            raise serializers.ValidationError(
                _("Give a reason somebody could audit later.")
            )
        return value.strip()


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class ProductCategoryViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Shop categories — the tab strip of the till."""

    capability_prefix = "pos"
    # Selling (``pos.add``) must not imply reshaping the catalogue, so the write
    # actions are lifted to ``pos.change`` exactly as the HTML views are.
    capability_overrides = {
        "create": "pos.change",
        "update": "pos.change",
        "partial_update": "pos.change",
        "destroy": "pos.delete",
    }
    queryset = selectors.category_queryset()
    serializer_class = ProductCategorySerializer
    filterset_fields = ["is_active", "parent"]
    search_fields = ["name", "code"]
    ordering_fields = ["sort_order", "name", "created_at"]
    ordering = ["sort_order", "name"]


class ProductViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Everything the shop sells, with live stock and margin."""

    capability_prefix = "pos"
    capability_overrides = {
        "create": "pos.change",
        "update": "pos.change",
        "partial_update": "pos.change",
        "destroy": "pos.delete",
        "adjust_stock": "pos.change",
        "movements": "pos.view",
        "low_stock": "pos.view",
        "valuation": "pos.view",
        "lookup": "pos.view",
    }
    queryset = selectors.product_queryset()
    serializer_class = ProductSerializer
    filterset_fields = ["category", "is_active", "track_stock", "unit"]
    search_fields = ["name", "sku", "barcode", "supplier", "description"]
    ordering_fields = ["name", "sku", "sale_price", "stock_quantity", "created_at"]
    ordering = ["sort_order", "name"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ProductWriteSerializer
        return ProductSerializer

    def perform_create(self, serializer):
        opening = serializer.validated_data.pop("opening_stock", ZERO)
        product = serializer.save(
            created_by=self.request.user, updated_by=self.request.user
        )
        services.set_opening_stock(
            product, opening, user=self.request.user, reference=product.sku
        )
        services.recalculate_stock(product)

    def perform_update(self, serializer):
        serializer.validated_data.pop("opening_stock", None)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        """Withdraw rather than erase — sale history points at this row."""
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        instance.delete()

    @extend_schema(request=StockAdjustmentSerializer, responses=StockMovementSerializer)
    @action(detail=True, methods=["post"], url_path="adjust-stock")
    def adjust_stock(self, request, pk=None):
        """Append a signed correction to this product's stock ledger."""
        product = self.get_object()
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            movement = services.adjust_stock(
                product,
                serializer.validated_data["quantity"],
                serializer.validated_data["reason"],
                user=request.user,
                movement_type=serializer.validated_data["movement_type"],
                reference=serializer.validated_data.get("reference", ""),
                request=request,
            )
        except DjangoValidationError as error:
            return Response(
                _from_validation_error(error), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            StockMovementSerializer(movement, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(responses=StockMovementSerializer(many=True))
    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        """This product's stock ledger, newest first."""
        product = self.get_object()
        queryset = selectors.movements_for_product(product, limit=200)
        return Response(
            StockMovementSerializer(
                queryset, many=True, context=self.get_serializer_context()
            ).data
        )

    @extend_schema(responses=ProductSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="low-stock")
    def low_stock(self, request):
        """Everything at or below its reorder point."""
        queryset = services.low_stock_products()
        return Response(
            ProductSerializer(
                queryset, many=True, context=self.get_serializer_context()
            ).data
        )

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"])
    def valuation(self, request):
        """What the shelves are worth, at cost and at retail."""
        return Response(services.stock_valuation())

    @extend_schema(
        parameters=[
            OpenApiParameter("code", str, description="Barcode or SKU.", required=True)
        ],
        responses=ProductSerializer,
    )
    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Resolve a scanned barcode (or a typed SKU) to one product."""
        code = request.query_params.get("code", "")
        if not code:
            return Response(
                _error(_("Provide ?code= with a barcode or SKU.")),
                status=status.HTTP_400_BAD_REQUEST,
            )
        product = selectors.find_by_barcode(code)
        if product is None:
            return Response(
                _error(
                    _("No product matches “%(code)s”.") % {"code": code},
                    error_type="not_found",
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            ProductSerializer(product, context=self.get_serializer_context()).data
        )


class SaleViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Receipts. Read-only except through ``checkout`` and ``void``."""

    capability_prefix = "pos"
    capability_overrides = {
        "checkout": "pos.add",
        "void": "pos.delete",
        "summary": "pos.view",
        "top_products": "pos.view",
    }
    queryset = selectors.sale_detail_queryset()
    serializer_class = SaleSerializer
    filterset_fields = ["status", "payment_method", "cashier", "customer"]
    search_fields = ["sale_number", "note", "customer__first_name", "customer__last_name"]
    ordering_fields = ["sold_at", "total_amount", "sale_number"]
    ordering = ["-sold_at"]

    @extend_schema(request=CheckoutSerializer, responses=SaleSerializer)
    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """Ring up a complete sale in one call.

        Builds a throw-away cart so the API and the till share exactly one code
        path — including the stock lock and the finance mirror.
        """
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        cart = services.SessionCart.detached()
        try:
            for line in data["items"]:
                cart.add(line["product"], line["quantity"])
                if line.get("discount_amount"):
                    cart.set_line_discount(line["product"], line["discount_amount"])
            if data.get("discount_percent"):
                cart.apply_discount(percent=data["discount_percent"])
            elif data.get("discount_amount"):
                cart.apply_discount(amount=data["discount_amount"])
        except DjangoValidationError as error:
            return Response(
                _from_validation_error(error), status=status.HTTP_400_BAD_REQUEST
            )

        customer = None
        if data.get("customer"):
            customer_model = services.get_model("customers", "Customer")
            if customer_model is not None:
                customer = customer_model.objects.filter(pk=data["customer"]).first()
                if customer is None:
                    return Response(
                        _error(_("That customer could not be found.")),
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        try:
            sale = services.complete_sale(
                cart,
                customer=customer,
                payment_method=data["payment_method"],
                amount_tendered=data.get("amount_tendered"),
                user=request.user,
                note=data.get("note", ""),
                request=request,
            )
        except DjangoValidationError as error:
            return Response(
                _from_validation_error(error), status=status.HTTP_400_BAD_REQUEST
            )

        sale = selectors.sale_detail_queryset().get(pk=sale.pk)
        return Response(
            SaleSerializer(sale, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=VoidSerializer, responses=SaleSerializer)
    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Reverse a completed sale, putting the stock back."""
        sale = self.get_object()
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.void_sale(
                sale, serializer.validated_data["reason"], user=request.user, request=request
            )
        except DjangoValidationError as error:
            return Response(
                _from_validation_error(error), status=status.HTTP_400_BAD_REQUEST
            )
        sale = selectors.sale_detail_queryset().get(pk=sale.pk)
        return Response(SaleSerializer(sale, context=self.get_serializer_context()).data)

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Shop takings for ``?range=`` (the project-wide date vocabulary)."""
        start, end, label = parse_date_range(request)
        payload = services.sales_summary(start, end)
        payload["range_label"] = label
        payload["daily"] = [
            {
                "date": row["date"].isoformat(),
                "revenue": str(row["revenue"]),
                "count": row["count"],
            }
            for row in payload["daily"]
        ]
        payload["by_method"] = [
            {**row, "label": str(row["label"]), "revenue": str(row["revenue"])}
            for row in payload["by_method"]
        ]
        for key in ("gross", "discount", "tax", "revenue", "net_revenue", "average_sale", "units"):
            payload[key] = str(payload[key])
        return Response(payload)

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"], url_path="top-products")
    def top_products(self, request):
        """Best sellers for ``?range=``."""
        start, end, _label = parse_date_range(request)
        rows = services.top_products(start, end, limit=20)
        return Response(
            [
                {
                    **row,
                    "units": str(row["units"]),
                    "revenue": str(row["revenue"]),
                    "net_revenue": str(row["net_revenue"]),
                }
                for row in rows
            ]
        )


class StockMovementViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """The stock ledger. Append-only, so there is nothing to write here."""

    capability_prefix = "pos"
    queryset = selectors.movement_queryset()
    serializer_class = StockMovementSerializer
    filterset_fields = ["product", "movement_type", "sale"]
    search_fields = ["product__name", "product__sku", "reference", "note"]
    ordering_fields = ["created_at", "quantity"]
    ordering = ["-created_at"]


ROUTES = [
    ("pos-categories", ProductCategoryViewSet, "pos-productcategory"),
    ("pos-products", ProductViewSet, "pos-product"),
    ("pos-sales", SaleViewSet, "pos-sale"),
    ("pos-stock-movements", StockMovementViewSet, "pos-stockmovement"),
]
