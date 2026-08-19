"""Read queries for the shop.

Kept apart from :mod:`apps.pos.services` because these only shape data — they
never decide anything. Every screen and API endpoint starts from one of these so
the prefetching is written once and an N+1 cannot creep back into the terminal,
which renders the whole catalogue on a single page.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from .models import Product, ProductCategory, Sale, SaleItem, StockMovement

ZERO = Decimal("0.00")
BALANCE_FIELD = DecimalField(max_digits=12, decimal_places=2)


# ---------------------------------------------------------------------------
# Stock ledger
# ---------------------------------------------------------------------------
def ledger_balance_subquery() -> Subquery:
    """Sum of every ledger row for the outer product.

    A correlated subquery rather than a join aggregate: it composes with the
    other annotations without multiplying rows, and it behaves identically on
    SQLite and PostgreSQL.
    """
    return Subquery(
        StockMovement.objects.filter(product=OuterRef("pk"))
        .order_by()
        .values("product")
        .annotate(total=Sum("quantity"))
        .values("total")[:1],
        output_field=BALANCE_FIELD,
    )


def with_ledger_balance(queryset: QuerySet[Product]) -> QuerySet[Product]:
    """Annotate ``ledger_balance`` — the exact on-hand quantity."""
    return queryset.annotate(
        ledger_balance=Coalesce(
            ledger_balance_subquery(), Value(ZERO), output_field=BALANCE_FIELD
        )
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def category_queryset(*, include_inactive: bool = True) -> QuerySet[ProductCategory]:
    queryset = ProductCategory.objects.select_related("parent")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.annotate(
        product_count=Count("products", filter=Q(products__is_deleted=False), distinct=True),
        active_product_count=Count(
            "products",
            filter=Q(products__is_deleted=False, products__is_active=True),
            distinct=True,
        ),
    ).order_by("sort_order", "name")


def selling_categories() -> QuerySet[ProductCategory]:
    """Active categories that actually have something to sell in them."""
    return (
        ProductCategory.objects.filter(is_active=True)
        .annotate(
            sellable_count=Count(
                "products",
                filter=Q(products__is_deleted=False, products__is_active=True),
                distinct=True,
            )
        )
        .filter(sellable_count__gt=0)
        .order_by("sort_order", "name")
    )


def product_queryset(*, include_inactive: bool = True) -> QuerySet[Product]:
    """The standard product queryset: category joined, ledger balance annotated."""
    queryset = Product.objects.select_related("category", "category__parent")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return with_ledger_balance(queryset)


def terminal_products(
    *, category=None, search: str = "", include_out_of_stock: bool = True
) -> QuerySet[Product]:
    """The grid on the left of the till screen."""
    queryset = product_queryset(include_inactive=False)
    if category is not None:
        # A parent category shows everything filed underneath it too, so the
        # "Wax" tab is not empty just because every product sits in a subtype.
        queryset = queryset.filter(Q(category=category) | Q(category__parent=category))
    if search:
        queryset = queryset.search(search)
    if not include_out_of_stock:
        queryset = queryset.filter(Q(track_stock=False) | Q(stock_quantity__gt=0))
    return queryset.order_by("sort_order", "name")


def find_by_barcode(code: str) -> Product | None:
    """Resolve a scanned code. Falls back to the SKU, which staff also type."""
    code = (code or "").strip()
    if not code:
        return None
    queryset = product_queryset(include_inactive=False)
    return (
        queryset.filter(Q(barcode__iexact=code) | Q(sku__iexact=code))
        .order_by("-barcode", "sku")
        .first()
    )


def low_stock_queryset() -> QuerySet[Product]:
    return product_queryset(include_inactive=False).low_stock().order_by(
        "stock_quantity", "name"
    )


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
def sale_item_prefetch() -> Prefetch:
    return Prefetch(
        "items",
        queryset=SaleItem.objects.select_related("product", "product__category").order_by("id"),
    )


def sale_queryset() -> QuerySet[Sale]:
    """List queryset: no line query per row, counters annotated."""
    return (
        Sale.objects.select_related("customer", "cashier")
        .annotate(line_count=Count("items", distinct=True))
        .order_by("-sold_at", "-id")
    )


def sale_detail_queryset() -> QuerySet[Sale]:
    return (
        Sale.objects.select_related("customer", "cashier", "created_by", "updated_by")
        .prefetch_related(
            sale_item_prefetch(),
            Prefetch(
                "stock_movements",
                queryset=StockMovement.objects.select_related("product").order_by("id"),
            ),
        )
    )


def movement_queryset() -> QuerySet[StockMovement]:
    return StockMovement.objects.select_related(
        "product", "product__category", "sale", "created_by"
    ).order_by("-created_at", "-id")


def movements_for_product(product: Product, limit: int = 50) -> QuerySet[StockMovement]:
    return movement_queryset().filter(product=product)[:limit]


def sale_line_totals(sale: Sale) -> dict:
    """Aggregate a single receipt without walking the lines in Python."""
    return sale.items.aggregate(
        units=Coalesce(Sum("quantity"), Value(ZERO), output_field=BALANCE_FIELD),
        gross=Coalesce(Sum("line_total"), Value(ZERO), output_field=BALANCE_FIELD),
        tax=Coalesce(Sum("tax_amount"), Value(ZERO), output_field=BALANCE_FIELD),
        line_discounts=Coalesce(
            Sum("discount_amount"), Value(ZERO), output_field=BALANCE_FIELD
        ),
    )
