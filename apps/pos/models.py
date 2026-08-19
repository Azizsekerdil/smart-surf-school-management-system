"""Shop catalogue, till receipts and the stock ledger.

Tax convention
--------------
``Product.sale_price`` is **tax inclusive** — it is the price on the shelf label
and the amount the customer pays. ``tax_rate`` therefore describes how much of
that price is tax, and every "tax" amount stored on a sale is the *contained*
tax, not an addition. The consequence is worth stating plainly because other
modules read these rows::

    Sale.subtotal        gross value of the lines, tax included
    Sale.discount_amount reduction applied to the whole sale
    Sale.total_amount    subtotal - discount           <- what was charged
    Sale.tax_amount      the tax already inside total_amount

So ``subtotal - discount == total`` always holds, and ``tax_amount`` is never
added to it.

Stock convention
----------------
:class:`StockMovement` is an append-only ledger: every row records a signed
quantity and the resulting balance. ``Product.stock_quantity`` is a denormalised
integer cache of that ledger, refreshed by :mod:`apps.pos.services`. Products
sold by volume or weight can hold a fractional balance; the integer cache floors
it (so the counter never claims stock that is not there) while
``Product.stock_balance`` keeps the exact figure for anything that decides.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import PaymentMethod
from apps.core.models import BaseModel, TimeStampedModel, money_field, percent_field
from apps.core.validators import validate_image_upload

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")

#: Prefix of the human-facing receipt number (``S-000001``).
SALE_NUMBER_PREFIX = "S-"
SALE_NUMBER_WIDTH = 6


def quantize_money(value) -> Decimal:
    """Round a Decimal to two places, half-up — the rule used by every till."""
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def contained_tax(gross: Decimal, tax_rate: Decimal) -> Decimal:
    """Return the tax already inside a tax-inclusive *gross* amount.

    ``gross - gross / (1 + rate/100)``. A zero or missing rate yields zero, and
    a nonsensical negative rate is treated as zero rather than inventing a
    credit.
    """
    gross = quantize_money(gross)
    rate = Decimal(str(tax_rate or 0))
    if rate <= 0 or gross == ZERO:
        return ZERO
    net = gross / (Decimal("1") + rate / HUNDRED)
    return quantize_money(gross - net)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class ProductCategory(TimeStampedModel):
    """A node in the shop taxonomy (Wax › Cold water, Drinks, Apparel …).

    Kept as data rather than an enum: every school stocks a different mix, and
    the terminal uses these as the tab strip above the product grid.
    """

    code = models.SlugField(
        _("code"),
        max_length=40,
        unique=True,
        help_text=_("Stable identifier used by imports and integrations, e.g. wax."),
    )
    name = models.CharField(_("name"), max_length=100)
    parent = models.ForeignKey(
        "self",
        verbose_name=_("parent category"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    icon = models.CharField(
        _("icon"),
        max_length=40,
        default="package",
        help_text=_("Name of a vendored Lucide icon, e.g. shopping-cart."),
    )
    sort_order = models.PositiveIntegerField(
        _("sort order"),
        default=100,
        help_text=_("Lower numbers appear first on the terminal tab strip."),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("product category")
        verbose_name_plural = _("product categories")
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["parent", "sort_order"], name="pos_cat_parent_order"),
            models.Index(fields=["is_active", "sort_order"], name="pos_cat_active_order"),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """Reject a parent chain that loops back onto this category."""
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": _("A category cannot be its own parent.")})
        seen: set[int] = {self.pk} if self.pk else set()
        node = self.parent
        guard = 0
        while node is not None and guard < 20:
            if node.pk in seen:
                raise ValidationError({"parent": _("This parent would create a loop.")})
            seen.add(node.pk)
            node = node.parent
            guard += 1

    @property
    def full_path(self) -> str:
        """``"Wax › Cold water"`` — the whole ancestor chain."""
        parts = [self.name]
        node = self.parent
        guard = 0
        while node is not None and guard < 10:
            parts.append(node.name)
            node = node.parent
            guard += 1
        return " › ".join(reversed(parts))


class ProductQuerySet(models.QuerySet):
    """Read helpers shared by the terminal, the API and the stock screens."""

    def active(self):
        return self.filter(is_active=True)

    def sellable(self):
        return self.filter(is_active=True)

    def tracked(self):
        return self.filter(track_stock=True)

    def low_stock(self):
        """Tracked products at or below their reorder threshold."""
        return self.filter(
            track_stock=True,
            is_active=True,
            stock_quantity__lte=models.F("low_stock_threshold"),
        )

    def out_of_stock(self):
        return self.filter(track_stock=True, is_active=True, stock_quantity__lte=0)

    def search(self, term: str):
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            models.Q(name__icontains=term)
            | models.Q(sku__icontains=term)
            | models.Q(barcode__icontains=term)
            | models.Q(description__icontains=term)
            | models.Q(supplier__icontains=term)
        )


class ProductManager(models.Manager.from_queryset(ProductQuerySet)):
    """Default manager: hides soft-deleted rows (see ``SoftDeleteManager``)."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ProductAllObjectsManager(models.Manager.from_queryset(ProductQuerySet)):
    """Escape hatch: includes withdrawn products, used by admin and audits."""


class Product(BaseModel):
    """Something the shop sells across the counter."""

    class Unit(models.TextChoices):
        PIECE = "piece", _("Piece")
        PAIR = "pair", _("Pair")
        SET = "set", _("Set")
        LITRE = "litre", _("Litre")
        KG = "kg", _("Kilogram")

    # --- identity ---------------------------------------------------------
    sku = models.CharField(
        _("SKU"),
        max_length=40,
        unique=True,
        help_text=_("Internal article number. Printed on the shelf label."),
    )
    barcode = models.CharField(
        _("barcode"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("EAN / UPC as printed on the packaging. Scanned at the till."),
    )
    name = models.CharField(_("name"), max_length=150, db_index=True)
    description = models.TextField(_("description"), blank=True)
    category = models.ForeignKey(
        "pos.ProductCategory",
        verbose_name=_("category"),
        on_delete=models.PROTECT,
        related_name="products",
    )

    # --- money ------------------------------------------------------------
    cost_price = money_field(
        _("cost price"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("What the school pays the supplier, per unit."),
    )
    sale_price = money_field(
        _("sale price"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Shelf price, tax included — exactly what the customer pays."),
    )
    tax_rate = percent_field(
        _("tax rate (%)"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100.00"))],
        help_text=_("Share of the shelf price that is tax. 0 for exempt goods."),
    )

    # --- stock ------------------------------------------------------------
    stock_quantity = models.IntegerField(
        _("stock on hand"),
        default=0,
        db_index=True,
        help_text=_("Derived from the stock ledger — never edit it directly."),
    )
    low_stock_threshold = models.PositiveIntegerField(
        _("reorder point"),
        default=5,
        help_text=_("At or below this level the product is flagged for reordering."),
    )
    track_stock = models.BooleanField(
        _("track stock"),
        default=True,
        help_text=_("Turn off for services and made-to-order items with no shelf count."),
    )
    unit = models.CharField(
        _("unit"),
        max_length=10,
        choices=Unit.choices,
        default=Unit.PIECE,
        db_index=True,
    )

    # --- presentation & sourcing -----------------------------------------
    photo = models.ImageField(
        _("photo"),
        upload_to="pos/products/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    supplier = models.CharField(_("supplier"), max_length=150, blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveIntegerField(
        _("sort order"),
        default=100,
        help_text=_("Lower numbers appear first in the terminal grid."),
    )

    objects = ProductManager()
    all_objects = ProductAllObjectsManager()

    class Meta:
        verbose_name = _("product")
        verbose_name_plural = _("products")
        ordering = ["sort_order", "name"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["is_active", "category"], name="pos_prod_active_cat"),
            models.Index(fields=["is_active", "sort_order"], name="pos_prod_active_order"),
            models.Index(fields=["track_stock", "stock_quantity"], name="pos_prod_stock"),
        ]
        constraints = [
            # A scanned barcode must resolve to exactly one live product,
            # otherwise the till would silently pick a row at random.
            models.UniqueConstraint(
                fields=["barcode"],
                condition=models.Q(is_deleted=False) & ~models.Q(barcode=""),
                name="pos_product_unique_barcode",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        errors: dict[str, object] = {}

        if self.sku:
            self.sku = self.sku.strip().upper()
        if self.barcode:
            self.barcode = self.barcode.strip()
            if not self.barcode.isdigit():
                errors["barcode"] = _("A barcode contains digits only.")
            else:
                clash = (
                    type(self)
                    .objects.filter(barcode=self.barcode)
                    .exclude(pk=self.pk)
                    .exists()
                )
                if clash:
                    errors["barcode"] = _("Another product already uses this barcode.")

        if self.cost_price is not None and self.cost_price < ZERO:
            errors["cost_price"] = _("The cost price cannot be negative.")
        if self.sale_price is not None and self.sale_price < ZERO:
            errors["sale_price"] = _("The sale price cannot be negative.")
        if self.tax_rate is not None and not (ZERO <= self.tax_rate <= Decimal("100")):
            errors["tax_rate"] = _("The tax rate must be between 0 and 100 percent.")

        if errors:
            raise ValidationError(errors)

    # -- derived money -----------------------------------------------------
    @property
    def tax_component(self) -> Decimal:
        """The tax already contained in one unit of ``sale_price``."""
        return contained_tax(self.sale_price, self.tax_rate)

    @property
    def net_price(self) -> Decimal:
        """Shelf price with the tax taken out — the school's actual revenue."""
        return quantize_money(quantize_money(self.sale_price) - self.tax_component)

    @property
    def margin_amount(self) -> Decimal:
        """Profit per unit, measured on the net price. Tax is not income."""
        return quantize_money(self.net_price - quantize_money(self.cost_price))

    @property
    def margin_percent(self) -> Decimal:
        """Margin as a share of the net price (``None``-free: 0 when unpriced)."""
        net = self.net_price
        if net <= ZERO:
            return ZERO
        return (self.margin_amount / net * HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)

    # -- derived stock -----------------------------------------------------
    @property
    def stock_balance(self) -> Decimal:
        """Exact on-hand quantity.

        Prefers a ``ledger_balance`` annotation (see :mod:`apps.pos.selectors`)
        so a grid of 200 products costs one query, and falls back to the integer
        cache when the product was loaded plainly.
        """
        annotated = self.__dict__.get("ledger_balance")
        if annotated is not None:
            return Decimal(str(annotated)).quantize(CENT)
        return Decimal(self.stock_quantity or 0).quantize(CENT)

    @property
    def is_low_stock(self) -> bool:
        if not self.track_stock:
            return False
        return self.stock_balance <= Decimal(self.low_stock_threshold or 0)

    @property
    def is_out_of_stock(self) -> bool:
        if not self.track_stock:
            return False
        return self.stock_balance <= ZERO

    @property
    def stock_value(self) -> Decimal:
        """On-hand quantity valued at cost — what the shelf is worth."""
        if not self.track_stock:
            return ZERO
        return quantize_money(max(self.stock_balance, ZERO) * quantize_money(self.cost_price))

    @property
    def stock_display(self) -> str:
        """``"12 piece"`` / ``"0.50 litre"`` — never a bare number."""
        balance = self.stock_balance
        label = self.get_unit_display()
        if balance == balance.to_integral_value():
            return f"{int(balance)} {label}"
        return f"{balance.normalize():f} {label}"

    @property
    def allows_fractional_quantity(self) -> bool:
        """Volume and weight may be sold in parts; a leash may not."""
        return self.unit in CONTINUOUS_UNITS

    @property
    def photo_url(self) -> str:
        try:
            return self.photo.url if self.photo else ""
        except ValueError:
            return ""


#: Units that can meaningfully be sold in fractions.
CONTINUOUS_UNITS: tuple[str, ...] = (Product.Unit.LITRE, Product.Unit.KG)
#: Units that must be whole numbers — half a leash is not a thing.
DISCRETE_UNITS: tuple[str, ...] = (Product.Unit.PIECE, Product.Unit.PAIR, Product.Unit.SET)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
class SaleQuerySet(models.QuerySet):
    def completed(self):
        return self.filter(status=Sale.Status.COMPLETED)

    def voided(self):
        return self.filter(status=Sale.Status.VOIDED)

    def counting(self):
        """Sales that count towards revenue — voided ones never do."""
        return self.filter(status__in=(Sale.Status.COMPLETED, Sale.Status.REFUNDED))

    def in_period(self, start, end):
        queryset = self
        if start is not None:
            queryset = queryset.filter(sold_at__gte=start)
        if end is not None:
            queryset = queryset.filter(sold_at__lte=end)
        return queryset


class SaleManager(models.Manager.from_queryset(SaleQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SaleAllObjectsManager(models.Manager.from_queryset(SaleQuerySet)):
    """Escape hatch manager: includes soft-deleted rows."""


class Sale(BaseModel):
    """One trip through the till.

    A completed sale is immutable. Corrections happen through
    :func:`apps.pos.services.void_sale`, which writes compensating stock
    movements and leaves this row exactly as it was rung up.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        COMPLETED = "completed", _("Completed")
        VOIDED = "voided", _("Voided")
        REFUNDED = "refunded", _("Refunded")

    sale_number = models.CharField(
        _("receipt number"),
        max_length=20,
        unique=True,
        blank=True,
        help_text=_("Generated automatically, e.g. S-000001."),
    )
    sold_at = models.DateTimeField(_("sold at"), default=timezone.now, db_index=True)

    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        help_text=_("Optional — walk-in sales stay anonymous."),
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("cashier"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
    )

    # --- money (see the tax convention in the module docstring) -----------
    subtotal = money_field(_("subtotal"), validators=[MinValueValidator(ZERO)])
    discount_amount = money_field(_("discount"), validators=[MinValueValidator(ZERO)])
    discount_percent = percent_field(
        _("discount (%)"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100.00"))],
    )
    tax_amount = money_field(
        _("tax included"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Tax already contained in the total — not added to it."),
    )
    total_amount = money_field(_("total"), validators=[MinValueValidator(ZERO)])
    paid_amount = money_field(_("tendered"), validators=[MinValueValidator(ZERO)])
    change_given = money_field(_("change given"), validators=[MinValueValidator(ZERO)])

    payment_method = models.CharField(
        _("payment method"),
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=15,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    note = models.TextField(_("note"), blank=True)
    voided_at = models.DateTimeField(_("voided at"), null=True, blank=True)
    void_reason = models.TextField(_("void reason"), blank=True)

    objects = SaleManager()
    all_objects = SaleAllObjectsManager()

    class Meta:
        verbose_name = _("sale")
        verbose_name_plural = _("sales")
        ordering = ["-sold_at", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["status", "sold_at"], name="pos_sale_status_date"),
            models.Index(fields=["cashier", "sold_at"], name="pos_sale_cashier_date"),
            models.Index(fields=["customer", "sold_at"], name="pos_sale_customer_date"),
        ]

    def __str__(self) -> str:
        return self.sale_number or _("Unnumbered sale")

    def clean(self) -> None:
        errors: dict[str, object] = {}
        if self.discount_percent is not None and not (
            ZERO <= self.discount_percent <= Decimal("100")
        ):
            errors["discount_percent"] = _("The discount must be between 0 and 100 percent.")
        if self.status == self.Status.VOIDED and not (self.void_reason or "").strip():
            errors["void_reason"] = _("Say why the sale was voided.")
        if errors:
            raise ValidationError(errors)

    # -- derived -----------------------------------------------------------
    @property
    def item_count(self) -> int:
        """Number of lines on the receipt."""
        return self.items.count()

    @property
    def total_quantity(self) -> Decimal:
        """Units sold across every line."""
        total = ZERO
        for item in self.items.all():
            total += Decimal(str(item.quantity or 0))
        return total.quantize(CENT)

    @property
    def net_amount(self) -> Decimal:
        """Total with the contained tax removed."""
        return quantize_money(quantize_money(self.total_amount) - quantize_money(self.tax_amount))

    @property
    def is_voided(self) -> bool:
        return self.status == self.Status.VOIDED

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def can_be_voided(self) -> bool:
        return self.status == self.Status.COMPLETED

    # -- arithmetic --------------------------------------------------------
    def recalculate(self, *, save: bool = True) -> Sale:
        """Recompute every monetary field from the lines.

        A percentage discount always wins over a typed amount, so the two can
        never drift apart. The contained tax is scaled down with the discount —
        selling for less means collecting less tax.
        """
        subtotal = ZERO
        gross_tax = ZERO
        for item in self.items.all():
            subtotal += quantize_money(item.line_total)
            gross_tax += quantize_money(item.tax_amount)

        subtotal = quantize_money(subtotal)
        gross_tax = quantize_money(gross_tax)

        percent = Decimal(str(self.discount_percent or 0))
        if percent > ZERO:
            discount = quantize_money(subtotal * percent / HUNDRED)
        else:
            discount = quantize_money(self.discount_amount)
        discount = min(max(discount, ZERO), subtotal)

        total = quantize_money(subtotal - discount)
        if subtotal > ZERO:
            tax = quantize_money(gross_tax * total / subtotal)
        else:
            tax = ZERO

        paid = quantize_money(self.paid_amount)
        change = quantize_money(max(ZERO, paid - total))

        self.subtotal = subtotal
        self.discount_amount = discount
        self.tax_amount = tax
        self.total_amount = total
        self.change_given = change

        if save and self.pk:
            self.save(
                update_fields=[
                    "subtotal",
                    "discount_amount",
                    "tax_amount",
                    "total_amount",
                    "change_given",
                    "updated_at",
                ]
            )
        return self

    def save(self, *args, **kwargs):
        if not self.sale_number:
            from apps.core.utils import next_sequential_code

            self.sale_number = next_sequential_code(
                type(self), "sale_number", SALE_NUMBER_PREFIX, width=SALE_NUMBER_WIDTH
            )
        return super().save(*args, **kwargs)


class SaleItem(TimeStampedModel):
    """One line on a receipt.

    ``unit_price`` and ``tax_amount`` are snapshots: repricing a product next
    week must not rewrite last week's receipts.
    """

    sale = models.ForeignKey(
        "pos.Sale",
        verbose_name=_("sale"),
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "pos.Product",
        verbose_name=_("product"),
        on_delete=models.PROTECT,
        related_name="sale_items",
    )
    quantity = models.DecimalField(
        _("quantity"),
        max_digits=8,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    unit_price = money_field(
        _("unit price"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Shelf price at the moment of sale, tax included."),
    )
    discount_amount = money_field(_("line discount"), validators=[MinValueValidator(ZERO)])
    tax_amount = money_field(_("tax included"), validators=[MinValueValidator(ZERO)])
    line_total = money_field(_("line total"), validators=[MinValueValidator(ZERO)])

    class Meta:
        verbose_name = _("sale item")
        verbose_name_plural = _("sale items")
        ordering = ["id"]
        indexes = [
            models.Index(fields=["sale", "product"], name="pos_item_sale_product"),
            models.Index(fields=["product"], name="pos_item_product"),
        ]

    def __str__(self) -> str:
        name = self.product.name if self.product_id else "?"
        return f"{self.quantity} × {name}"

    def clean(self) -> None:
        errors: dict[str, object] = {}
        quantity = Decimal(str(self.quantity or 0))
        if quantity <= ZERO:
            errors["quantity"] = _("The quantity must be greater than zero.")
        elif (
            self.product_id
            and self.product.unit in DISCRETE_UNITS
            and quantity != quantity.to_integral_value()
        ):
            errors["quantity"] = _("This product is sold in whole units only.")
        if self.discount_amount is not None and self.discount_amount < ZERO:
            errors["discount_amount"] = _("A line discount cannot be negative.")
        if errors:
            raise ValidationError(errors)

    def compute_total(self) -> Decimal:
        """Recompute ``line_total`` and the contained ``tax_amount``.

        The line discount is capped at the gross value so a fat-fingered
        discount can never produce a negative line.
        """
        quantity = Decimal(str(self.quantity or 0))
        unit_price = quantize_money(self.unit_price)
        gross = quantize_money(quantity * unit_price)

        discount = min(max(quantize_money(self.discount_amount), ZERO), gross)
        self.discount_amount = discount

        self.line_total = quantize_money(gross - discount)
        rate = self.product.tax_rate if self.product_id else ZERO
        self.tax_amount = contained_tax(self.line_total, rate)
        return self.line_total

    @property
    def net_total(self) -> Decimal:
        return quantize_money(quantize_money(self.line_total) - quantize_money(self.tax_amount))


# ---------------------------------------------------------------------------
# Stock ledger
# ---------------------------------------------------------------------------
class StockMovement(TimeStampedModel):
    """One append-only entry in the stock ledger.

    ``quantity`` is signed: goods arriving are positive, goods leaving are
    negative. ``balance_after`` is the running total at the moment the row was
    written, so a stock take can be reconciled against any point in history
    without replaying the whole ledger.
    """

    class MovementType(models.TextChoices):
        PURCHASE = "purchase", _("Purchase received")
        SALE = "sale", _("Sold")
        RETURN = "return", _("Returned to stock")
        ADJUSTMENT = "adjustment", _("Stock take adjustment")
        DAMAGE = "damage", _("Damaged / written off")
        TRANSFER = "transfer", _("Transfer")
        INITIAL = "initial", _("Opening balance")

    product = models.ForeignKey(
        "pos.Product",
        verbose_name=_("product"),
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(
        _("movement"),
        max_length=15,
        choices=MovementType.choices,
        default=MovementType.ADJUSTMENT,
        db_index=True,
    )
    quantity = models.DecimalField(
        _("quantity"),
        max_digits=8,
        decimal_places=2,
        help_text=_("Signed: negative when stock leaves the shelf."),
    )
    balance_after = models.DecimalField(
        _("balance after"), max_digits=10, decimal_places=2, default=ZERO
    )
    reference = models.CharField(
        _("reference"),
        max_length=80,
        blank=True,
        db_index=True,
        help_text=_("Receipt number, supplier invoice or stock-take reference."),
    )
    note = models.TextField(_("note"), blank=True)
    sale = models.ForeignKey(
        "pos.Sale",
        verbose_name=_("sale"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("recorded by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pos_stock_movements",
    )

    class Meta:
        verbose_name = _("stock movement")
        verbose_name_plural = _("stock movements")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["product", "-created_at"], name="pos_move_product_date"),
            models.Index(fields=["movement_type", "-created_at"], name="pos_move_type_date"),
            models.Index(fields=["sale"], name="pos_move_sale"),
        ]

    def __str__(self) -> str:
        sign = "+" if (self.quantity or 0) >= 0 else ""
        return f"{self.get_movement_type_display()} {sign}{self.quantity}"

    def clean(self) -> None:
        if self.quantity is None or Decimal(str(self.quantity)) == ZERO:
            raise ValidationError({"quantity": _("A movement of zero changes nothing.")})

    @property
    def is_incoming(self) -> bool:
        return Decimal(str(self.quantity or 0)) > ZERO

    @property
    def signed_display(self) -> str:
        quantity = Decimal(str(self.quantity or 0))
        sign = "+" if quantity > ZERO else ""
        return f"{sign}{quantity.normalize():f}"

    @property
    def integral_balance(self) -> int:
        """The ledger balance floored to a whole unit, matching the cache."""
        return int(
            Decimal(str(self.balance_after or 0)).to_integral_value(rounding=ROUND_FLOOR)
        )
