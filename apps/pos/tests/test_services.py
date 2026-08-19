from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import PaymentMethod
from apps.pos import services
from apps.pos.models import Product, Sale, StockMovement

from .factories import ProductFactory, UserFactory, stocked_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier(db):
    return UserFactory(username="cashier")


@pytest.fixture
def cart():
    return services.SessionCart.detached()


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
def test_opening_stock_writes_one_ledger_row():
    product = stocked_product(quantity=12)
    movements = StockMovement.objects.filter(product=product)
    assert movements.count() == 1
    assert movements.first().movement_type == StockMovement.MovementType.INITIAL
    assert services.ledger_balance(product) == Decimal("12.00")


def test_stock_quantity_is_recomputed_from_the_ledger(cashier):
    product = stocked_product(quantity=10)
    services.adjust_stock(product, Decimal("-3"), "Broken in transit", cashier,
                          movement_type=StockMovement.MovementType.DAMAGE)
    product.refresh_from_db()
    assert product.stock_quantity == 7
    assert services.ledger_balance(product) == Decimal("7.00")


def test_a_correction_appends_rather_than_edits(cashier):
    product = stocked_product(quantity=10)
    services.adjust_stock(product, Decimal("5"), "Delivery", cashier,
                          movement_type=StockMovement.MovementType.PURCHASE)
    services.adjust_stock(product, Decimal("-2"), "Stock take", cashier)
    rows = list(StockMovement.objects.filter(product=product).order_by("id"))
    assert [row.quantity for row in rows] == [Decimal("10.00"), Decimal("5.00"), Decimal("-2.00")]
    assert [row.balance_after for row in rows] == [
        Decimal("10.00"),
        Decimal("15.00"),
        Decimal("13.00"),
    ]


def test_stock_cannot_be_driven_negative(cashier):
    product = stocked_product(quantity=2)
    with pytest.raises(ValidationError):
        services.adjust_stock(product, Decimal("-5"), "Typo", cashier)
    assert services.ledger_balance(product) == Decimal("2.00")


def test_adjustment_requires_a_reason(cashier):
    product = stocked_product(quantity=2)
    with pytest.raises(ValidationError):
        services.adjust_stock(product, Decimal("1"), "   ", cashier)


def test_adjustment_writes_an_audit_entry(cashier):
    product = stocked_product(quantity=2)
    services.adjust_stock(product, Decimal("4"), "Delivery from supplier", cashier)
    assert AuditLog.objects.filter(action=AuditAction.UPDATE).exists()


def test_untracked_products_cannot_be_adjusted(cashier):
    product = ProductFactory(track_stock=False)
    with pytest.raises(ValidationError):
        services.adjust_stock(product, Decimal("1"), "Delivery", cashier)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
def test_cart_add_accumulates_the_same_product(cart):
    product = stocked_product(quantity=10)
    cart.add(product, Decimal("2"))
    cart.add(product, Decimal("3"))
    lines = cart.lines()
    assert len(lines) == 1
    assert lines[0].quantity == Decimal("5.00")


def test_cart_validates_stock_at_add_time(cart):
    product = stocked_product(quantity=2)
    with pytest.raises(ValidationError):
        cart.add(product, Decimal("3"))
    assert cart.is_empty


def test_cart_validates_stock_across_repeated_adds(cart):
    product = stocked_product(quantity=2)
    cart.add(product, Decimal("2"))
    with pytest.raises(ValidationError):
        cart.add(product, Decimal("1"))


def test_cart_refuses_a_fraction_of_a_discrete_unit(cart):
    product = stocked_product(quantity=10, unit=Product.Unit.PIECE)
    with pytest.raises(ValidationError):
        cart.add(product, Decimal("1.5"))


def test_cart_allows_a_fraction_of_a_continuous_unit(cart):
    product = stocked_product(quantity=10, unit=Product.Unit.LITRE)
    cart.add(product, Decimal("1.5"))
    assert cart.lines()[0].quantity == Decimal("1.50")


def test_cart_ignores_untracked_stock(cart):
    product = ProductFactory(track_stock=False)
    cart.add(product, Decimal("99"))
    assert cart.lines()[0].quantity == Decimal("99.00")


def test_cart_refuses_an_inactive_product(cart):
    product = stocked_product(quantity=5, is_active=False)
    with pytest.raises(ValidationError):
        cart.add(product, Decimal("1"))


def test_cart_drops_a_product_withdrawn_after_it_was_added(cart):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("1"))
    Product.objects.filter(pk=product.pk).update(is_active=False)

    fresh = services.SessionCart(cart.session)
    assert fresh.lines() == []
    assert fresh.warnings


def test_setting_quantity_to_zero_removes_the_line(cart):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("2"))
    cart.set_quantity(product, Decimal("0"))
    assert cart.is_empty


def test_percentage_discount_replaces_a_fixed_one(cart):
    product = stocked_product(quantity=5, sale_price=Decimal("100.00"), tax_rate=Decimal("0.00"))
    cart.add(product, Decimal("1"))
    cart.apply_discount(amount=Decimal("10.00"))
    cart.apply_discount(percent=Decimal("25.00"))
    totals = cart.totals()
    assert totals.discount_percent == Decimal("25.00")
    assert totals.discount_amount == Decimal("25.00")
    assert totals.total_amount == Decimal("75.00")


def test_a_discount_cannot_exceed_the_cart_total(cart):
    product = stocked_product(quantity=5, sale_price=Decimal("50.00"))
    cart.add(product, Decimal("1"))
    with pytest.raises(ValidationError):
        cart.apply_discount(amount=Decimal("80.00"))


def test_cart_totals_extract_the_contained_tax(cart):
    product = stocked_product(quantity=5, sale_price=Decimal("120.00"), tax_rate=Decimal("20.00"))
    cart.add(product, Decimal("2"))
    totals = cart.totals()
    assert totals.subtotal == Decimal("240.00")
    assert totals.total_amount == Decimal("240.00")
    assert totals.tax_amount == Decimal("40.00")
    assert totals.net_amount == Decimal("200.00")


# ---------------------------------------------------------------------------
# Completing a sale
# ---------------------------------------------------------------------------
def test_complete_sale_writes_everything_once(cart, cashier):
    product = stocked_product(quantity=10, sale_price=Decimal("120.00"), tax_rate=Decimal("20.00"))
    cart.add(product, Decimal("2"))

    sale = services.complete_sale(
        cart,
        customer=None,
        payment_method=PaymentMethod.CASH,
        amount_tendered=Decimal("300.00"),
        user=cashier,
    )

    assert sale.status == Sale.Status.COMPLETED
    assert sale.total_amount == Decimal("240.00")
    assert sale.tax_amount == Decimal("40.00")
    assert sale.paid_amount == Decimal("300.00")
    assert sale.change_given == Decimal("60.00")
    assert sale.cashier == cashier
    assert sale.items.count() == 1

    product.refresh_from_db()
    assert product.stock_quantity == 8
    assert StockMovement.objects.filter(sale=sale, movement_type="sale").count() == 1
    assert cart.is_empty


def test_complete_sale_records_a_payment_audit_entry(cart, cashier):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("1"))
    services.complete_sale(cart, user=cashier, amount_tendered=Decimal("120.00"))
    assert AuditLog.objects.filter(action=AuditAction.PAYMENT).exists()


def test_cash_tendered_below_the_total_is_refused(cart, cashier):
    product = stocked_product(quantity=5, sale_price=Decimal("120.00"))
    cart.add(product, Decimal("1"))
    with pytest.raises(ValidationError):
        services.complete_sale(cart, user=cashier, amount_tendered=Decimal("50.00"))
    # Nothing was written: the stock is untouched and no sale exists.
    assert Sale.objects.count() == 0
    assert services.ledger_balance(product) == Decimal("5.00")


def test_card_payments_never_produce_change(cart, cashier):
    product = stocked_product(quantity=5, sale_price=Decimal("120.00"))
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(
        cart,
        payment_method=PaymentMethod.CARD,
        amount_tendered=Decimal("500.00"),
        user=cashier,
    )
    assert sale.paid_amount == Decimal("120.00")
    assert sale.change_given == Decimal("0.00")


def test_completing_an_empty_cart_is_refused(cart, cashier):
    with pytest.raises(ValidationError):
        services.complete_sale(cart, user=cashier)


def test_stock_sold_out_between_add_and_checkout_is_caught(cart, cashier):
    product = stocked_product(quantity=3)
    cart.add(product, Decimal("3"))
    # Another till empties the shelf in the meantime.
    services.adjust_stock(product, Decimal("-3"), "Sold at the other till", cashier)

    with pytest.raises(ValidationError):
        services.complete_sale(cart, user=cashier, amount_tendered=Decimal("1000.00"))
    assert Sale.objects.count() == 0


def test_untracked_products_write_no_stock_movement(cart, cashier):
    product = ProductFactory(track_stock=False, sale_price=Decimal("50.00"))
    cart.add(product, Decimal("2"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("100.00"))
    assert StockMovement.objects.filter(sale=sale).count() == 0


# ---------------------------------------------------------------------------
# Voiding
# ---------------------------------------------------------------------------
def test_void_reverses_the_stock_without_deleting_anything(cart, cashier):
    product = stocked_product(quantity=10)
    cart.add(product, Decimal("4"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("1000.00"))
    assert services.ledger_balance(product) == Decimal("6.00")

    services.void_sale(sale, "Customer changed their mind", user=cashier)

    sale.refresh_from_db()
    assert sale.status == Sale.Status.VOIDED
    assert sale.voided_at is not None
    assert sale.items.count() == 1  # the receipt is intact
    assert services.ledger_balance(product) == Decimal("10.00")
    assert StockMovement.objects.filter(
        sale=sale, movement_type=StockMovement.MovementType.RETURN
    ).count() == 1


def test_void_requires_a_reason(cart, cashier):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("500.00"))
    with pytest.raises(ValidationError):
        services.void_sale(sale, "  ", user=cashier)


def test_a_sale_cannot_be_voided_twice(cart, cashier):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("500.00"))
    services.void_sale(sale, "Rang up the wrong item", user=cashier)
    with pytest.raises(ValidationError):
        services.void_sale(sale, "Again", user=cashier)


def test_void_records_a_refund_audit_entry(cart, cashier):
    product = stocked_product(quantity=5)
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("500.00"))
    services.void_sale(sale, "Duplicate receipt", user=cashier)
    assert AuditLog.objects.filter(action=AuditAction.REFUND).exists()


# ---------------------------------------------------------------------------
# The finance mirror
# ---------------------------------------------------------------------------
def _finance_payment_model():
    return services.get_model("finance", "Payment")


@pytest.fixture
def shop_customer(db):
    customer_model = services.get_model("customers", "Customer")
    if customer_model is None:  # pragma: no cover - customers is a hard dependency
        pytest.skip("the customers module is not installed")
    return customer_model.objects.create(first_name="Deniz", last_name="Kaya")


@pytest.fixture
def finance_manager(db):
    """Voiding needs ``finance.refund``; every role holding ``pos.delete`` has it."""
    from apps.accounts.constants import Role

    return UserFactory(username="finance-manager", role=Role.MANAGER)


def test_a_named_sale_raises_a_shop_payment(cart, finance_manager, shop_customer):
    payment_model = _finance_payment_model()
    if payment_model is None:
        pytest.skip("the finance module is not installed")

    product = stocked_product(quantity=10, sale_price=Decimal("120.00"))
    cart.add(product, Decimal("2"))
    sale = services.complete_sale(
        cart, customer=shop_customer, user=finance_manager, amount_tendered=Decimal("300.00")
    )

    payment = payment_model.objects.get(reference=sale.sale_number)
    assert payment.amount == Decimal("240.00")
    assert payment.category == "shop"
    assert payment.customer_id == shop_customer.pk


def test_an_anonymous_sale_raises_no_payment(cart, cashier):
    payment_model = _finance_payment_model()
    if payment_model is None:
        pytest.skip("the finance module is not installed")

    product = stocked_product(quantity=10)
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("500.00"))

    # A walk-in has no receivables account; the takings still count in the POS
    # reports, which are computed from the sales themselves.
    assert not payment_model.objects.filter(reference=sale.sale_number).exists()
    assert sale.status == Sale.Status.COMPLETED


def test_voiding_reverses_the_shop_payment(cart, finance_manager, shop_customer):
    payment_model = _finance_payment_model()
    if payment_model is None:
        pytest.skip("the finance module is not installed")

    product = stocked_product(quantity=10, sale_price=Decimal("100.00"))
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(
        cart, customer=shop_customer, user=finance_manager, amount_tendered=Decimal("100.00")
    )

    services.void_sale(sale, "Customer changed their mind", user=finance_manager)

    rows = payment_model.objects.filter(reference=sale.sale_number)
    assert rows.count() == 2  # the original and its negative counterpart
    assert sum(row.amount for row in rows) == Decimal("0.00")


def test_a_finance_failure_never_loses_the_sale(cart, cashier, shop_customer, monkeypatch):
    """The customer has paid; bookkeeping trouble must not undo that."""
    import apps.finance.services as finance_services

    def explode(*args, **kwargs):
        raise RuntimeError("finance is having a bad day")

    monkeypatch.setattr(finance_services, "record_payment", explode)

    product = stocked_product(quantity=10, sale_price=Decimal("50.00"))
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(
        cart, customer=shop_customer, user=cashier, amount_tendered=Decimal("50.00")
    )

    assert sale.status == Sale.Status.COMPLETED
    product.refresh_from_db()
    assert product.stock_quantity == 9


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_stock_valuation_counts_only_tracked_active_products():
    stocked_product(quantity=4, cost_price=Decimal("10.00"), sale_price=Decimal("25.00"))
    ProductFactory(track_stock=False, cost_price=Decimal("999.00"))

    valuation = services.stock_valuation()
    assert valuation["cost_value"] == Decimal("40.00")
    assert valuation["retail_value"] == Decimal("100.00")
    assert valuation["potential_margin"] == Decimal("60.00")


def test_low_stock_products_lists_what_needs_reordering():
    low = stocked_product(quantity=2, low_stock_threshold=5, name="Leash")
    stocked_product(quantity=50, low_stock_threshold=5, name="Wax")
    assert [product.pk for product in services.low_stock_products()] == [low.pk]


def test_sales_summary_excludes_voided_sales(cart, cashier):
    product = stocked_product(quantity=10, sale_price=Decimal("100.00"), tax_rate=Decimal("0.00"))
    cart.add(product, Decimal("1"))
    kept = services.complete_sale(cart, user=cashier, amount_tendered=Decimal("100.00"))

    second = services.SessionCart.detached()
    second.add(product, Decimal("1"))
    voided = services.complete_sale(second, user=cashier, amount_tendered=Decimal("100.00"))
    services.void_sale(voided, "Wrong customer", user=cashier)

    summary = services.sales_summary(None, None)
    assert summary["sale_count"] == 1
    assert summary["revenue"] == Decimal("100.00")
    assert summary["voided_count"] == 1
    assert summary["average_sale"] == Decimal("100.00")
    assert kept.status == Sale.Status.COMPLETED


def test_top_products_ranks_by_revenue(cart, cashier):
    cheap = stocked_product(quantity=50, sale_price=Decimal("10.00"), name="Sticker")
    dear = stocked_product(quantity=50, sale_price=Decimal("500.00"), name="Wetsuit")
    cart.add(cheap, Decimal("3"))
    cart.add(dear, Decimal("1"))
    services.complete_sale(cart, user=cashier, amount_tendered=Decimal("1000.00"))

    rows = services.top_products(None, None)
    assert rows[0]["name"] == "Wetsuit"
    assert rows[0]["revenue"] == Decimal("500.00")
    assert rows[1]["units"] == Decimal("3.00")


def test_cashier_summary_totals_each_till_operator(cart, cashier):
    product = stocked_product(quantity=10, sale_price=Decimal("60.00"))
    cart.add(product, Decimal("2"))
    services.complete_sale(cart, user=cashier, amount_tendered=Decimal("120.00"))

    rows = services.cashier_summary(None, None)
    assert len(rows) == 1
    assert rows[0]["revenue"] == Decimal("120.00")
    assert rows[0]["count"] == 1
