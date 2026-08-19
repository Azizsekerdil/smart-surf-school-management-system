"""Business rules for the shop.

Everything that changes money or stock lives here, is wrapped in a transaction
and works in :class:`~decimal.Decimal` from end to end. Three rules are worth
knowing before reading further:

* **The ledger is the truth.** Stock is never written directly; it is derived by
  appending a :class:`~apps.pos.models.StockMovement` and recomputing the cached
  counter from the whole ledger.
* **Sales are never deleted.** :func:`void_sale` writes compensating movements
  and marks the receipt voided, so the till roll stays complete.
* **This module does not import :mod:`apps.finance` at load time.** The shop
  must work on a deployment where the finance module is absent, so the payment
  is raised through a lazy lookup and written on its own savepoint — a finance
  failure can never lose a sale the customer has already paid for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import PaymentMethod

from . import selectors
from .models import (
    DISCRETE_UNITS,
    Product,
    Sale,
    SaleItem,
    StockMovement,
    contained_tax,
    quantize_money,
)

logger = logging.getLogger("apps.pos")

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
QUANTITY_STEP = Decimal("0.01")
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)

#: Where the cart lives between requests. One cart per browser session, which is
#: exactly one till — two staff on one machine share a session and a cart.
CART_SESSION_KEY = "pos_cart"

#: Payment methods that can produce change. Everything else is taken to the cent.
CHANGE_GIVING_METHODS = (PaymentMethod.CASH,)

#: Category slug written onto the finance payment, per the finance contract.
FINANCE_CATEGORY_SHOP = "shop"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class PosError(ValidationError):
    """Base class for every refusal this module issues."""


class CartError(PosError):
    """The cart operation cannot be performed (stock, unit or state)."""


class StockError(PosError):
    """The stock movement would leave the ledger in an impossible state."""


class SaleError(PosError):
    """The sale cannot be completed or voided."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def get_model(app_label: str, model_name: str):
    """Return a model from another app, or ``None`` when it is unavailable."""
    try:
        return django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return None


def to_quantity(value, default: Decimal = ZERO) -> Decimal:
    """Coerce anything to a 2-dp Decimal quantity without raising."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value)).quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return default


def floor_units(value: Decimal) -> int:
    """Floor a ledger balance to whole units for the cached counter."""
    return int(Decimal(str(value or 0)).to_integral_value(rounding=ROUND_FLOOR))


# ---------------------------------------------------------------------------
# Stock ledger
# ---------------------------------------------------------------------------
def ledger_balance(product: Product) -> Decimal:
    """The exact on-hand quantity of *product*, straight from the ledger."""
    total = StockMovement.objects.filter(product=product).aggregate(
        total=Coalesce(Sum("quantity"), Value(ZERO), output_field=MONEY_FIELD)
    )["total"]
    return Decimal(str(total or 0)).quantize(QUANTITY_STEP)


def available_quantity(product: Product) -> Decimal:
    """How much of *product* may be sold right now.

    Untracked products (services, made to order) are always available; the
    caller should not be forced to special-case them.
    """
    if not product.track_stock:
        return Decimal("999999.00")
    return ledger_balance(product)


def recalculate_stock(product: Product) -> Decimal:
    """Refresh the cached counter from the ledger and return the exact balance."""
    balance = ledger_balance(product)
    cached = floor_units(balance)
    if product.stock_quantity != cached:
        Product.all_objects.filter(pk=product.pk).update(
            stock_quantity=cached, updated_at=timezone.now()
        )
    product.stock_quantity = cached
    product.__dict__["ledger_balance"] = balance
    return balance


def validate_quantity(product: Product, quantity: Decimal) -> Decimal:
    """Reject a quantity the product cannot be sold in."""
    quantity = to_quantity(quantity)
    if quantity <= ZERO:
        raise CartError(_("Enter a quantity greater than zero."))
    if product.unit in DISCRETE_UNITS and quantity != quantity.to_integral_value():
        raise CartError(
            _("%(product)s is sold in whole units only.") % {"product": product.name}
        )
    return quantity


@transaction.atomic
def write_movement(
    product: Product,
    *,
    quantity: Decimal,
    movement_type: str,
    reference: str = "",
    note: str = "",
    sale: Sale | None = None,
    user=None,
    allow_negative: bool = False,
) -> StockMovement:
    """Append one row to the ledger and refresh the cached counter.

    The product row is locked for the duration so two tills selling the last
    leash cannot both succeed. ``allow_negative`` exists only for corrections
    that deliberately reconstruct history.
    """
    quantity = to_quantity(quantity)
    if quantity == ZERO:
        raise StockError(_("A movement of zero changes nothing."))
    if product.unit in DISCRETE_UNITS and quantity != quantity.to_integral_value():
        raise StockError(
            _("%(product)s is counted in whole units only.") % {"product": product.name}
        )

    locked = Product.all_objects.select_for_update().filter(pk=product.pk).first()
    if locked is None:
        raise StockError(_("This product no longer exists."))

    balance = ledger_balance(locked) + quantity
    if balance < ZERO and not allow_negative:
        raise StockError(
            _("Not enough stock: %(name)s would drop below zero.") % {"name": locked.name}
        )

    movement = StockMovement(
        product=locked,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=balance.quantize(QUANTITY_STEP),
        reference=(reference or "")[:80],
        note=note or "",
        sale=sale,
        created_by=user if (user is not None and user.is_authenticated) else None,
    )
    movement.full_clean(exclude=["balance_after"])
    movement.save()

    recalculate_stock(locked)
    product.stock_quantity = locked.stock_quantity
    product.__dict__["ledger_balance"] = balance
    return movement


@transaction.atomic
def adjust_stock(
    product: Product,
    quantity,
    reason: str,
    user=None,
    *,
    movement_type: str = StockMovement.MovementType.ADJUSTMENT,
    reference: str = "",
    request=None,
) -> StockMovement:
    """Correct the shelf count by a signed delta.

    This is what a stock take, a delivery, a breakage and a supplier return all
    go through. It never rewrites a previous row — the correction *is* a new row,
    which is why yesterday's numbers still reconcile after today's fix.
    """
    quantity = to_quantity(quantity)
    if quantity == ZERO:
        raise StockError({"quantity": _("Enter a quantity other than zero.")})
    reason = (reason or "").strip()
    if not reason:
        raise StockError({"reason": _("Say why the stock is being corrected.")})
    if not product.track_stock:
        raise StockError(
            {"__all__": _("Stock is not tracked for this product. Turn tracking on first.")}
        )

    before = ledger_balance(product)
    movement = write_movement(
        product,
        quantity=quantity,
        movement_type=movement_type,
        reference=reference,
        note=reason,
        user=user,
    )
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=product,
        user=user,
        description=_("Stock of %(product)s corrected by %(delta)s (%(reason)s)")
        % {"product": product.name, "delta": movement.signed_display, "reason": reason},
        changes={"stock": [str(before), str(movement.balance_after)]},
    )
    return movement


@transaction.atomic
def set_opening_stock(product: Product, quantity, *, user=None, reference: str = "") -> StockMovement | None:
    """Write the opening balance of a freshly created product."""
    quantity = to_quantity(quantity)
    if quantity <= ZERO or not product.track_stock:
        return None
    return write_movement(
        product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.INITIAL,
        reference=reference,
        note=str(_("Opening balance")),
        user=user,
    )


# ---------------------------------------------------------------------------
# The cart
# ---------------------------------------------------------------------------
@dataclass
class CartLine:
    """One resolved line of the cart, priced but not yet persisted."""

    product: Product
    quantity: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    line_total: Decimal
    tax_amount: Decimal
    available: Decimal

    @property
    def gross(self) -> Decimal:
        return quantize_money(self.quantity * self.unit_price)

    @property
    def net_total(self) -> Decimal:
        return quantize_money(self.line_total - self.tax_amount)

    @property
    def exceeds_stock(self) -> bool:
        return self.product.track_stock and self.quantity > self.available


@dataclass
class CartTotals:
    """What the customer is about to pay."""

    subtotal: Decimal = ZERO
    discount_amount: Decimal = ZERO
    discount_percent: Decimal = ZERO
    tax_amount: Decimal = ZERO
    total_amount: Decimal = ZERO
    line_count: int = 0
    total_quantity: Decimal = ZERO

    @property
    def net_amount(self) -> Decimal:
        return quantize_money(self.total_amount - self.tax_amount)

    def as_dict(self) -> dict:
        return {
            "subtotal": str(self.subtotal),
            "discount_amount": str(self.discount_amount),
            "discount_percent": str(self.discount_percent),
            "tax_amount": str(self.tax_amount),
            "total_amount": str(self.total_amount),
            "net_amount": str(self.net_amount),
            "line_count": self.line_count,
            "total_quantity": str(self.total_quantity),
        }


class DetachedSession(dict):
    """A session-shaped dict for carts that never belong to a browser.

    The REST checkout endpoint builds one of these so the API and the till run
    through exactly the same cart code — including the stock checks — without
    inventing a second, subtly different code path.
    """

    modified = False


class SessionCart:
    """The open transaction at the till, held in the browser session.

    Nothing is written to the database until the sale is completed, so a
    customer who changes their mind costs nothing and an abandoned cart cannot
    hold stock hostage. Stock is checked when a line is added *and* again inside
    the completing transaction, because the shelf can empty in between.
    """

    def __init__(self, session):
        self.session = session
        raw = session.get(CART_SESSION_KEY)
        if not isinstance(raw, dict):
            raw = {}
        lines = raw.get("lines")
        self._lines: dict[str, dict] = lines if isinstance(lines, dict) else {}
        self.discount_percent = to_quantity(raw.get("discount_percent"))
        self.discount_amount = quantize_money(raw.get("discount_amount") or ZERO)
        self.customer_id = raw.get("customer_id") or None
        self.payment_method = raw.get("payment_method") or PaymentMethod.CASH
        self.note = raw.get("note") or ""
        #: Problems found while resolving the cart, shown once to the operator.
        self.warnings: list[str] = []
        self._resolved: list[CartLine] | None = None

    @classmethod
    def detached(cls) -> SessionCart:
        """An in-memory cart, for the API and for tests."""
        return cls(DetachedSession())

    # -- persistence -------------------------------------------------------
    def save(self) -> None:
        self.session[CART_SESSION_KEY] = {
            "lines": self._lines,
            "discount_percent": str(self.discount_percent),
            "discount_amount": str(self.discount_amount),
            "customer_id": self.customer_id,
            "payment_method": self.payment_method,
            "note": self.note,
        }
        self.session.modified = True

    def clear(self) -> None:
        self._lines = {}
        self.discount_percent = ZERO
        self.discount_amount = ZERO
        self.customer_id = None
        self.payment_method = PaymentMethod.CASH
        self.note = ""
        self._resolved = None
        self.save()

    # -- state -------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return not self.lines()

    @property
    def line_count(self) -> int:
        return len(self.lines())

    @property
    def total_quantity(self) -> Decimal:
        total = sum((line.quantity for line in self.lines()), ZERO)
        return Decimal(total).quantize(QUANTITY_STEP)

    def product_ids(self) -> list[int]:
        ids: list[int] = []
        for key in self._lines:
            try:
                ids.append(int(key))
            except (TypeError, ValueError):
                continue
        return ids

    def lines(self) -> list[CartLine]:
        """Resolve the stored ids into priced lines.

        A product that has been withdrawn or deactivated since it went into the
        cart is dropped with a warning rather than silently sold.
        """
        if self._resolved is not None:
            return self._resolved

        ids = self.product_ids()
        if not ids:
            self._resolved = []
            return self._resolved

        products = {
            product.pk: product
            for product in selectors.product_queryset().filter(pk__in=ids)
        }

        resolved: list[CartLine] = []
        dropped = False
        for key in list(self._lines):
            try:
                product_id = int(key)
            except (TypeError, ValueError):
                del self._lines[key]
                dropped = True
                continue

            product = products.get(product_id)
            if product is None or not product.is_active:
                label = product.name if product is not None else _("A removed product")
                self.warnings.append(
                    _("%(name)s is no longer for sale and was removed from the cart.")
                    % {"name": label}
                )
                del self._lines[key]
                dropped = True
                continue

            stored = self._lines[key] or {}
            quantity = to_quantity(stored.get("quantity"), Decimal("1.00"))
            if quantity <= ZERO:
                del self._lines[key]
                dropped = True
                continue

            resolved.append(self._price_line(product, quantity, stored.get("discount")))

        if dropped:
            self.save()
        self._resolved = resolved
        return resolved

    def _price_line(self, product: Product, quantity: Decimal, discount) -> CartLine:
        unit_price = quantize_money(product.sale_price)
        gross = quantize_money(quantity * unit_price)
        line_discount = min(max(quantize_money(discount or ZERO), ZERO), gross)
        line_total = quantize_money(gross - line_discount)
        # ``stock_balance`` reads the annotation that ``lines()`` already
        # fetched, so redrawing a ten-line cart stays at one query.
        available = product.stock_balance if product.track_stock else quantity
        return CartLine(
            product=product,
            quantity=quantity,
            unit_price=unit_price,
            discount_amount=line_discount,
            line_total=line_total,
            tax_amount=contained_tax(line_total, product.tax_rate),
            available=available,
        )

    def _invalidate(self) -> None:
        self._resolved = None

    # -- mutations ---------------------------------------------------------
    def add(self, product: Product, quantity=Decimal("1.00")) -> CartLine:
        """Add to an existing line, or start a new one. Validates stock."""
        quantity = validate_quantity(product, quantity)
        if not product.is_active:
            raise CartError(
                _("%(name)s is not for sale at the moment.") % {"name": product.name}
            )

        key = str(product.pk)
        current = to_quantity((self._lines.get(key) or {}).get("quantity"))
        wanted = (current + quantity).quantize(QUANTITY_STEP)
        self._assert_stock(product, wanted)

        entry = self._lines.setdefault(key, {"quantity": "0.00", "discount": "0.00"})
        entry["quantity"] = str(wanted)
        entry.setdefault("discount", "0.00")
        self._invalidate()
        self.save()
        return self._price_line(product, wanted, entry.get("discount"))

    def set_quantity(self, product: Product, quantity) -> CartLine | None:
        """Set an absolute quantity. Zero or less removes the line."""
        quantity = to_quantity(quantity)
        if quantity <= ZERO:
            self.remove(product)
            return None
        quantity = validate_quantity(product, quantity)
        self._assert_stock(product, quantity)

        key = str(product.pk)
        entry = self._lines.setdefault(key, {"quantity": "0.00", "discount": "0.00"})
        entry["quantity"] = str(quantity)
        entry.setdefault("discount", "0.00")
        self._invalidate()
        self.save()
        return self._price_line(product, quantity, entry.get("discount"))

    def remove(self, product) -> None:
        key = str(getattr(product, "pk", product))
        if key in self._lines:
            del self._lines[key]
            self._invalidate()
            self.save()

    def set_line_discount(self, product: Product, amount) -> CartLine | None:
        """Discount a single line by a fixed amount."""
        key = str(product.pk)
        entry = self._lines.get(key)
        if entry is None:
            return None
        quantity = to_quantity(entry.get("quantity"), Decimal("1.00"))
        gross = quantize_money(quantity * quantize_money(product.sale_price))
        amount = min(max(quantize_money(amount), ZERO), gross)
        entry["discount"] = str(amount)
        self._invalidate()
        self.save()
        return self._price_line(product, quantity, amount)

    def apply_discount(self, *, percent=None, amount=None) -> CartTotals:
        """Discount the whole sale, by percentage or by a fixed amount.

        Setting one clears the other: a receipt can show a percentage or an
        amount, never two competing discounts that disagree.
        """
        if percent is not None:
            percent = to_quantity(percent)
            if not (ZERO <= percent <= HUNDRED):
                raise CartError(_("The discount must be between 0 and 100 percent."))
            self.discount_percent = percent
            self.discount_amount = ZERO
        if amount is not None:
            amount = quantize_money(amount)
            if amount < ZERO:
                raise CartError(_("The discount cannot be negative."))
            subtotal = self._subtotal()
            if amount > subtotal:
                raise CartError(
                    _("The discount cannot exceed the sale total of %(total)s.")
                    % {"total": subtotal}
                )
            self.discount_amount = amount
            self.discount_percent = ZERO
        self.save()
        return self.totals()

    def clear_discount(self) -> CartTotals:
        self.discount_percent = ZERO
        self.discount_amount = ZERO
        self.save()
        return self.totals()

    def set_customer(self, customer) -> None:
        self.customer_id = getattr(customer, "pk", customer) or None
        self.save()

    def set_payment_method(self, method: str) -> None:
        if method not in dict(PaymentMethod.choices):
            raise CartError(_("Choose a payment method the till accepts."))
        self.payment_method = method
        self.save()

    def set_note(self, note: str) -> None:
        self.note = (note or "").strip()[:500]
        self.save()

    # -- money -------------------------------------------------------------
    def _subtotal(self) -> Decimal:
        return quantize_money(sum((line.line_total for line in self.lines()), ZERO))

    def totals(self) -> CartTotals:
        lines = self.lines()
        subtotal = quantize_money(sum((line.line_total for line in lines), ZERO))
        gross_tax = quantize_money(sum((line.tax_amount for line in lines), ZERO))

        if self.discount_percent > ZERO:
            discount = quantize_money(subtotal * self.discount_percent / HUNDRED)
        else:
            discount = quantize_money(self.discount_amount)
        discount = min(max(discount, ZERO), subtotal)

        total = quantize_money(subtotal - discount)
        tax = quantize_money(gross_tax * total / subtotal) if subtotal > ZERO else ZERO

        return CartTotals(
            subtotal=subtotal,
            discount_amount=discount,
            discount_percent=self.discount_percent,
            tax_amount=tax,
            total_amount=total,
            line_count=len(lines),
            total_quantity=Decimal(sum((line.quantity for line in lines), ZERO)).quantize(
                QUANTITY_STEP
            ),
        )

    def customer(self):
        """Resolve the attached customer, or ``None`` for a walk-in."""
        if not self.customer_id:
            return None
        model = get_model("customers", "Customer")
        if model is None:
            return None
        return model.objects.filter(pk=self.customer_id).first()

    # -- guards ------------------------------------------------------------
    def _assert_stock(self, product: Product, wanted: Decimal) -> None:
        if not product.track_stock:
            return
        available = available_quantity(product)
        if wanted > available:
            raise CartError(
                _("Only %(available)s of %(name)s left in stock.")
                % {"available": _format_quantity(available, product), "name": product.name}
            )


def _format_quantity(value: Decimal, product: Product) -> str:
    value = Decimal(str(value or 0))
    if value == value.to_integral_value():
        return f"{int(value)} {product.get_unit_display()}"
    return f"{value.normalize():f} {product.get_unit_display()}"


# ---------------------------------------------------------------------------
# Completing a sale
# ---------------------------------------------------------------------------
def _resolve_choice(model, field_name: str, wanted: str) -> str | None:
    """Return the choice on *model.field_name* that means *wanted*.

    The finance module owns its own vocabulary; this matches on the value first
    and the enum name second so ``SHOP`` / ``shop`` / ``Shop sale`` all resolve.
    """
    try:
        field = model._meta.get_field(field_name)
    except Exception:  # noqa: BLE001 - unknown field is a normal outcome here
        return None
    choices = getattr(field, "choices", None) or []
    wanted_lower = wanted.lower()
    for value, label in choices:
        if str(value).lower() == wanted_lower:
            return value
    for value, label in choices:
        if wanted_lower in str(label).lower():
            return value
    return None


def _finance_services():
    """Return :mod:`apps.finance.services`, or ``None`` when it is not installed.

    Imported lazily and by name: the shop has to run on a deployment that does
    not have the finance module, and importing it at module scope would make
    that impossible.
    """
    if not django_apps.is_installed("apps.finance"):
        return None
    try:
        from importlib import import_module

        return import_module("apps.finance.services")
    except ImportError:  # pragma: no cover - finance present but broken
        logger.exception("apps.finance is installed but its services cannot be imported")
        return None


def _shop_payments_for(sale: Sale):
    """Incoming finance payments raised by *sale*, matched on the receipt number."""
    payment_model = get_model("finance", "Payment")
    if payment_model is None:
        return []
    queryset = payment_model.objects.filter(reference=sale.sale_number)
    if "is_refund" in {
        f.name for f in payment_model._meta.get_fields() if getattr(f, "concrete", False)
    }:
        queryset = queryset.filter(is_refund=False)
    return list(queryset)


def _create_shop_payment(sale: Sale, *, user=None, request=None):
    """Mirror the sale into :mod:`apps.finance` as a paid shop payment.

    Goes through ``finance.services.record_payment`` so the invoice, booking and
    customer balances finance maintains stay correct — building the row by hand
    would bypass them. It runs on its own savepoint: a sale the customer has
    already paid for must never be rolled back because the bookkeeping side
    disagreed.

    Two cases end with no payment row, both deliberate:

    * finance is not installed — the shop still works standalone;
    * the sale is anonymous — finance requires a named customer, and inventing
      one would corrupt the receivables ledger. The takings are still complete
      in the POS reports, which are computed from the sales themselves.
    """
    finance = _finance_services()
    if finance is None or not hasattr(finance, "record_payment"):
        return None
    if sale.customer_id is None:
        logger.debug(
            "Shop sale %s is anonymous; no finance payment raised", sale.sale_number
        )
        return None
    if quantize_money(sale.total_amount) <= ZERO:
        return None

    payment_model = get_model("finance", "Payment")
    category = _resolve_choice(payment_model, "category", FINANCE_CATEGORY_SHOP)

    kwargs = {
        "method": sale.payment_method,
        "reference": sale.sale_number,
        "notes": _("Shop sale %(number)s") % {"number": sale.sale_number},
        "user": user,
        "request": request,
    }
    if category is not None:
        kwargs["category"] = category

    try:
        with transaction.atomic():
            return finance.record_payment(sale.customer, sale.total_amount, **kwargs)
    except Exception:  # noqa: BLE001 - the sale must survive a finance failure
        logger.exception("Could not mirror shop sale %s into finance", sale.sale_number)
        return None


def _refund_shop_payment(sale: Sale, reason: str, *, user=None, request=None) -> bool:
    """Reverse the finance payment behind a voided sale.

    Returns whether every payment found was reversed, so the caller can record
    the gap in the audit trail instead of letting it disappear into a log file.
    """
    finance = _finance_services()
    if finance is None or not hasattr(finance, "refund_payment"):
        return True

    payments = _shop_payments_for(sale)
    if not payments:
        return True

    complete = True
    for payment in payments:
        refundable = getattr(payment, "refundable_amount", None)
        if refundable is None or refundable <= ZERO:
            continue
        try:
            with transaction.atomic():
                finance.refund_payment(payment, refundable, reason, user=user, request=request)
        except Exception:  # noqa: BLE001 - the void must succeed regardless
            logger.exception(
                "Could not reverse the finance payment for shop sale %s", sale.sale_number
            )
            complete = False
    return complete


def _tendered_for(method: str, total: Decimal, amount_tendered) -> Decimal:
    """Work out what was actually handed over.

    Only cash produces change. A card, transfer or online payment is taken for
    the exact amount, so an operator typing a round number into the tendered box
    cannot make the till think it owes change it never gave.
    """
    if method not in CHANGE_GIVING_METHODS:
        return total
    tendered = quantize_money(amount_tendered) if amount_tendered not in (None, "") else total
    if tendered < total:
        raise SaleError(
            {
                "amount_tendered": _(
                    "The amount tendered (%(given)s) is less than the total (%(total)s)."
                )
                % {"given": tendered, "total": total}
            }
        )
    return tendered


@transaction.atomic
def complete_sale(
    cart: SessionCart,
    customer=None,
    payment_method: str = PaymentMethod.CASH,
    amount_tendered=None,
    user=None,
    *,
    note: str = "",
    request=None,
) -> Sale:
    """Turn the cart into a receipt: sale, lines, stock movements, payment.

    Every step happens in one transaction. Stock is re-checked against the
    locked ledger here — the check the cart did when the line was added is a
    courtesy to the operator, this one is the guarantee.
    """
    lines = cart.lines()
    if not lines:
        raise SaleError(_("There is nothing in the cart."))
    if payment_method not in dict(PaymentMethod.choices):
        raise SaleError({"payment_method": _("Choose a payment method the till accepts.")})

    # Lock every product involved, in a stable order, so two tills selling the
    # same items cannot deadlock against each other.
    product_ids = sorted(line.product.pk for line in lines)
    locked = {
        product.pk: product
        for product in Product.all_objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
    }

    for line in lines:
        product = locked.get(line.product.pk)
        if product is None or product.is_deleted or not product.is_active:
            raise SaleError(
                _("%(name)s is no longer for sale. Remove it from the cart.")
                % {"name": line.product.name}
            )
        if product.track_stock:
            on_hand = ledger_balance(product)
            if line.quantity > on_hand:
                raise SaleError(
                    _("Only %(available)s of %(name)s left in stock.")
                    % {
                        "available": _format_quantity(on_hand, product),
                        "name": product.name,
                    }
                )

    now = timezone.now()
    sale = Sale(
        sold_at=now,
        customer=customer,
        cashier=user if (user is not None and user.is_authenticated) else None,
        payment_method=payment_method,
        status=Sale.Status.DRAFT,
        discount_percent=cart.discount_percent,
        discount_amount=cart.discount_amount,
        note=(note or cart.note or "")[:2000],
        created_by=user if (user is not None and user.is_authenticated) else None,
        updated_by=user if (user is not None and user.is_authenticated) else None,
    )
    sale.full_clean(exclude=["sale_number"])
    sale.save()

    for line in lines:
        product = locked[line.product.pk]
        item = SaleItem(
            sale=sale,
            product=product,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount_amount=line.discount_amount,
        )
        item.compute_total()
        item.full_clean()
        item.save()

    sale.recalculate(save=True)

    tendered = _tendered_for(payment_method, sale.total_amount, amount_tendered)
    sale.paid_amount = tendered
    sale.status = Sale.Status.COMPLETED
    sale.recalculate(save=False)
    sale.save(
        update_fields=[
            "paid_amount",
            "change_given",
            "status",
            "subtotal",
            "discount_amount",
            "tax_amount",
            "total_amount",
            "updated_at",
        ]
    )

    for line in lines:
        product = locked[line.product.pk]
        if not product.track_stock:
            continue
        write_movement(
            product,
            quantity=-line.quantity,
            movement_type=StockMovement.MovementType.SALE,
            reference=sale.sale_number,
            sale=sale,
            user=user,
        )

    _create_shop_payment(sale, user=user, request=request)

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=sale,
        user=user,
        description=_("Shop sale %(number)s — %(total)s by %(method)s")
        % {
            "number": sale.sale_number,
            "total": sale.total_amount,
            "method": sale.get_payment_method_display(),
        },
        changes={
            "total_amount": [None, str(sale.total_amount)],
            "payment_method": [None, sale.payment_method],
            "items": [None, str(len(lines))],
        },
    )

    cart.clear()
    return sale


@transaction.atomic
def void_sale(sale: Sale, reason: str, user=None, *, request=None) -> Sale:
    """Reverse a completed sale without erasing it.

    Every stock movement the sale wrote gets a compensating ``RETURN`` row, so
    the ledger balances again and both the original mistake and its correction
    stay visible. The receipt itself is never touched.
    """
    reason = (reason or "").strip()
    if not reason:
        raise SaleError({"void_reason": _("Say why the sale is being voided.")})
    if sale.status == Sale.Status.VOIDED:
        raise SaleError(_("This sale has already been voided."))
    if sale.status != Sale.Status.COMPLETED:
        raise SaleError(
            _("Only a completed sale can be voided; this one is %(status)s.")
            % {"status": sale.get_status_display()}
        )

    items = list(sale.items.select_related("product"))
    product_ids = sorted({item.product_id for item in items})
    locked = {
        product.pk: product
        for product in Product.all_objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
    }

    for item in items:
        product = locked.get(item.product_id)
        if product is None or not product.track_stock:
            continue
        write_movement(
            product,
            quantity=item.quantity,
            movement_type=StockMovement.MovementType.RETURN,
            reference=sale.sale_number,
            note=_("Void: %(reason)s") % {"reason": reason},
            sale=sale,
            user=user,
        )

    sale.status = Sale.Status.VOIDED
    sale.voided_at = timezone.now()
    sale.void_reason = reason[:2000]
    if user is not None and user.is_authenticated:
        sale.updated_by = user
    sale.full_clean(exclude=["sale_number"])
    sale.save(
        update_fields=["status", "voided_at", "void_reason", "updated_by", "updated_at"]
    )

    money_reversed = _refund_shop_payment(sale, reason, user=user, request=request)

    changes = {"status": [Sale.Status.COMPLETED, Sale.Status.VOIDED]}
    if not money_reversed:
        # Never silent: the stock is back but the books are not, and somebody
        # with the finance capability has to finish the job by hand.
        changes["finance_reversal"] = [None, "failed"]
    record_audit(
        request,
        action=AuditAction.REFUND,
        instance=sale,
        user=user,
        description=(
            _("Shop sale %(number)s voided — %(reason)s")
            if money_reversed
            else _(
                "Shop sale %(number)s voided — %(reason)s. The finance payment "
                "could not be reversed automatically; refund it by hand."
            )
        )
        % {"number": sale.sale_number, "reason": reason},
        changes=changes,
    )
    return sale


# ---------------------------------------------------------------------------
# Catalogue maintenance
# ---------------------------------------------------------------------------
@transaction.atomic
def create_product(*, opening_stock=ZERO, user=None, request=None, **fields) -> Product:
    """Create a product and open its ledger with the counted shelf stock."""
    product = Product(**fields)
    if user is not None and user.is_authenticated:
        product.created_by = user
        product.updated_by = user
    product.full_clean()
    product.save()
    set_opening_stock(product, opening_stock, user=user, reference=product.sku)
    recalculate_stock(product)
    return product


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def low_stock_products(limit: int | None = None):
    """Active tracked products at or below their reorder point."""
    queryset = selectors.low_stock_queryset()
    return queryset[:limit] if limit else queryset


def stock_valuation() -> dict:
    """What the shelves are worth, at cost and at retail."""
    tracked = Product.objects.filter(is_active=True, track_stock=True)
    aggregates = tracked.aggregate(
        cost_value=Coalesce(
            Sum(
                F("stock_quantity") * F("cost_price"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Value(ZERO),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        retail_value=Coalesce(
            Sum(
                F("stock_quantity") * F("sale_price"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Value(ZERO),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        units=Coalesce(Sum("stock_quantity"), Value(0)),
        product_count=Count("id", distinct=True),
    )
    cost_value = quantize_money(aggregates["cost_value"])
    retail_value = quantize_money(aggregates["retail_value"])
    return {
        "cost_value": cost_value,
        "retail_value": retail_value,
        "potential_margin": quantize_money(retail_value - cost_value),
        "units": int(aggregates["units"] or 0),
        "product_count": aggregates["product_count"],
        "low_stock_count": Product.objects.low_stock().count(),
        "out_of_stock_count": Product.objects.out_of_stock().count(),
    }


def sales_summary(start: datetime | None = None, end: datetime | None = None) -> dict:
    """Headline shop numbers for a period, plus the daily series for the chart.

    Voided sales are excluded everywhere — a void means the money was never
    taken, so counting it would overstate both revenue and units sold.
    """
    queryset = Sale.objects.counting().in_period(start, end)

    aggregates = queryset.aggregate(
        sale_count=Count("id", distinct=True),
        gross=Coalesce(Sum("subtotal"), Value(ZERO), output_field=MONEY_FIELD),
        discount=Coalesce(Sum("discount_amount"), Value(ZERO), output_field=MONEY_FIELD),
        tax=Coalesce(Sum("tax_amount"), Value(ZERO), output_field=MONEY_FIELD),
        revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY_FIELD),
    )
    sale_count = aggregates["sale_count"] or 0
    revenue = quantize_money(aggregates["revenue"])
    tax = quantize_money(aggregates["tax"])

    units = SaleItem.objects.filter(sale__in=queryset).aggregate(
        total=Coalesce(Sum("quantity"), Value(ZERO), output_field=MONEY_FIELD)
    )["total"]

    by_method = [
        {
            "method": row["payment_method"],
            "label": dict(PaymentMethod.choices).get(
                row["payment_method"], row["payment_method"]
            ),
            "count": row["count"],
            "revenue": quantize_money(row["revenue"]),
        }
        for row in queryset.values("payment_method")
        .annotate(
            count=Count("id"),
            revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY_FIELD),
        )
        .order_by("-revenue")
    ]

    daily = [
        {
            "date": row["day"],
            "revenue": quantize_money(row["revenue"]),
            "count": row["count"],
        }
        for row in queryset.annotate(day=TruncDate("sold_at"))
        .values("day")
        .annotate(
            revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY_FIELD),
            count=Count("id"),
        )
        .order_by("day")
    ]

    voided = Sale.objects.voided().in_period(start, end).count()

    return {
        "sale_count": sale_count,
        "gross": quantize_money(aggregates["gross"]),
        "discount": quantize_money(aggregates["discount"]),
        "tax": tax,
        "revenue": revenue,
        "net_revenue": quantize_money(revenue - tax),
        "units": Decimal(str(units or 0)).quantize(QUANTITY_STEP),
        "average_sale": quantize_money(revenue / sale_count) if sale_count else ZERO,
        "by_method": by_method,
        "daily": daily,
        "voided_count": voided,
    }


def top_products(
    start: datetime | None = None, end: datetime | None = None, limit: int = 10
) -> list[dict]:
    """Best sellers of the period, by revenue."""
    sales = Sale.objects.counting().in_period(start, end)
    rows = (
        SaleItem.objects.filter(sale__in=sales)
        .values("product_id", "product__name", "product__sku", "product__unit")
        .annotate(
            units=Coalesce(Sum("quantity"), Value(ZERO), output_field=MONEY_FIELD),
            revenue=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY_FIELD),
            tax=Coalesce(Sum("tax_amount"), Value(ZERO), output_field=MONEY_FIELD),
            line_count=Count("id"),
        )
        .order_by("-revenue")[:limit]
    )
    return [
        {
            "product_id": row["product_id"],
            "name": row["product__name"],
            "sku": row["product__sku"],
            "unit": row["product__unit"],
            "units": Decimal(str(row["units"] or 0)).quantize(QUANTITY_STEP),
            "revenue": quantize_money(row["revenue"]),
            "net_revenue": quantize_money(
                quantize_money(row["revenue"]) - quantize_money(row["tax"])
            ),
            "line_count": row["line_count"],
        }
        for row in rows
    ]


def cashier_summary(start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """Takings per cashier — the number each person reconciles their till against."""
    rows = (
        Sale.objects.counting()
        .in_period(start, end)
        .values("cashier_id", "cashier__first_name", "cashier__last_name", "cashier__username")
        .annotate(
            count=Count("id"),
            revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY_FIELD),
        )
        .order_by("-revenue")
    )
    summary = []
    for row in rows:
        name = " ".join(
            part
            for part in (row["cashier__first_name"], row["cashier__last_name"])
            if part
        ).strip()
        summary.append(
            {
                "cashier_id": row["cashier_id"],
                "name": name or row["cashier__username"] or str(_("Unknown")),
                "count": row["count"],
                "revenue": quantize_money(row["revenue"]),
            }
        )
    return summary
