from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.pos.models import Product, Sale, SaleItem, contained_tax

from .factories import ProductCategoryFactory, ProductFactory, SaleFactory, stocked_product

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def test_category_str_and_full_path():
    parent = ProductCategoryFactory(name="Wax", code="wax")
    child = ProductCategoryFactory(name="Cold water", code="wax-cold", parent=parent)
    assert str(child) == "Cold water"
    assert child.full_path == "Wax › Cold water"


def test_category_rejects_a_parent_loop():
    first = ProductCategoryFactory(code="a")
    second = ProductCategoryFactory(code="b", parent=first)
    first.parent = second
    with pytest.raises(ValidationError):
        first.clean()


# ---------------------------------------------------------------------------
# Product money
# ---------------------------------------------------------------------------
def test_contained_tax_is_extracted_not_added():
    # 120.00 at 20% VAT contains 20.00 of tax, not 24.00.
    assert contained_tax(Decimal("120.00"), Decimal("20.00")) == Decimal("20.00")
    assert contained_tax(Decimal("120.00"), Decimal("0.00")) == Decimal("0.00")
    assert contained_tax(Decimal("0.00"), Decimal("20.00")) == Decimal("0.00")


def test_margin_is_measured_on_the_net_price():
    product = ProductFactory(
        cost_price=Decimal("40.00"), sale_price=Decimal("120.00"), tax_rate=Decimal("20.00")
    )
    assert product.net_price == Decimal("100.00")
    assert product.margin_amount == Decimal("60.00")
    assert product.margin_percent == Decimal("60.00")


def test_margin_percent_is_zero_for_a_free_product():
    product = ProductFactory(cost_price=Decimal("0.00"), sale_price=Decimal("0.00"))
    assert product.margin_percent == Decimal("0.00")


def test_product_str_carries_the_sku():
    product = ProductFactory(name="Cold water wax", sku="WAX-01")
    assert str(product) == "Cold water wax (WAX-01)"


def test_product_rejects_a_non_numeric_barcode():
    product = ProductFactory.build(barcode="not-a-barcode", category=ProductCategoryFactory())
    with pytest.raises(ValidationError) as error:
        product.clean()
    assert "barcode" in error.value.message_dict


def test_product_rejects_a_duplicate_barcode():
    ProductFactory(barcode="8690000000000")
    duplicate = ProductFactory.build(barcode="8690000000000", category=ProductCategoryFactory())
    with pytest.raises(ValidationError) as error:
        duplicate.clean()
    assert "barcode" in error.value.message_dict


# ---------------------------------------------------------------------------
# Product stock
# ---------------------------------------------------------------------------
def test_stock_flags_follow_the_ledger():
    product = stocked_product(quantity=3, low_stock_threshold=5)
    assert product.stock_quantity == 3
    assert product.is_low_stock is True
    assert product.is_out_of_stock is False


def test_untracked_products_are_never_low_or_out():
    product = ProductFactory(track_stock=False, low_stock_threshold=100)
    assert product.is_low_stock is False
    assert product.is_out_of_stock is False
    assert product.stock_value == Decimal("0.00")


def test_stock_value_is_at_cost():
    product = stocked_product(quantity=4, cost_price=Decimal("25.50"))
    assert product.stock_value == Decimal("102.00")


def test_stock_display_names_the_unit():
    product = stocked_product(quantity=2, unit=Product.Unit.PAIR)
    assert product.stock_display == "2 Pair"


# ---------------------------------------------------------------------------
# Sale arithmetic
# ---------------------------------------------------------------------------
def _sale_with_line(quantity="2", unit_price="120.00", tax_rate="20.00", **sale_kwargs):
    product = ProductFactory(sale_price=Decimal(unit_price), tax_rate=Decimal(tax_rate))
    sale = SaleFactory(**sale_kwargs)
    item = SaleItem(sale=sale, product=product, quantity=Decimal(quantity), unit_price=Decimal(unit_price))
    item.compute_total()
    item.save()
    return sale, item


def test_sale_number_is_generated_sequentially():
    first = SaleFactory()
    second = SaleFactory()
    assert first.sale_number == "S-000001"
    assert second.sale_number == "S-000002"


def test_line_total_and_contained_tax():
    _sale, item = _sale_with_line(quantity="2", unit_price="120.00")
    assert item.line_total == Decimal("240.00")
    assert item.tax_amount == Decimal("40.00")
    assert item.net_total == Decimal("200.00")


def test_line_discount_is_capped_at_the_gross_value():
    product = ProductFactory(sale_price=Decimal("50.00"), tax_rate=Decimal("0.00"))
    sale = SaleFactory()
    item = SaleItem(
        sale=sale,
        product=product,
        quantity=Decimal("1"),
        unit_price=Decimal("50.00"),
        discount_amount=Decimal("999.00"),
    )
    item.compute_total()
    assert item.discount_amount == Decimal("50.00")
    assert item.line_total == Decimal("0.00")


def test_recalculate_applies_a_percentage_discount_and_scales_the_tax():
    sale, _item = _sale_with_line(quantity="2", unit_price="120.00")
    sale.discount_percent = Decimal("10.00")
    sale.recalculate()

    assert sale.subtotal == Decimal("240.00")
    assert sale.discount_amount == Decimal("24.00")
    assert sale.total_amount == Decimal("216.00")
    # Selling for less collects less tax: 20% of the discounted gross.
    assert sale.tax_amount == Decimal("36.00")
    assert sale.net_amount == Decimal("180.00")


def test_recalculate_never_lets_a_discount_exceed_the_subtotal():
    sale, _item = _sale_with_line(quantity="1", unit_price="100.00")
    sale.discount_amount = Decimal("500.00")
    sale.recalculate()
    assert sale.discount_amount == Decimal("100.00")
    assert sale.total_amount == Decimal("0.00")


def test_recalculate_computes_change_from_the_tendered_amount():
    sale, _item = _sale_with_line(quantity="1", unit_price="120.00")
    sale.paid_amount = Decimal("200.00")
    sale.recalculate()
    assert sale.change_given == Decimal("80.00")


def test_item_count_and_total_quantity():
    sale, _item = _sale_with_line(quantity="3", unit_price="10.00")
    assert sale.item_count == 1
    assert sale.total_quantity == Decimal("3.00")


def test_sale_requires_a_reason_when_voided():
    sale = SaleFactory(status=Sale.Status.VOIDED)
    with pytest.raises(ValidationError) as error:
        sale.clean()
    assert "void_reason" in error.value.message_dict


def test_sale_item_rejects_a_fraction_of_a_discrete_unit():
    product = ProductFactory(unit=Product.Unit.PIECE)
    sale = SaleFactory()
    item = SaleItem(
        sale=sale, product=product, quantity=Decimal("1.50"), unit_price=Decimal("10.00")
    )
    with pytest.raises(ValidationError) as error:
        item.clean()
    assert "quantity" in error.value.message_dict


def test_sale_item_allows_a_fraction_of_a_continuous_unit():
    product = ProductFactory(unit=Product.Unit.LITRE)
    sale = SaleFactory()
    item = SaleItem(
        sale=sale, product=product, quantity=Decimal("1.50"), unit_price=Decimal("10.00")
    )
    item.clean()  # must not raise
    item.compute_total()
    assert item.line_total == Decimal("15.00")
