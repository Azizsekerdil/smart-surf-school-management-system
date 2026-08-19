from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.pos import services
from apps.pos.models import Product, Sale, StockMovement

from .factories import ProductCategoryFactory, stocked_product

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="apimanager", email="apimanager@example.test", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def receptionist(db):
    return User.objects.create_user(
        username="apireception", email="apireception@example.test", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def outsider(db):
    return User.objects.create_user(
        username="apiguest", email="apiguest@example.test", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_products_require_authentication(api):
    assert api.get(reverse("pos-product-list")).status_code in (401, 403)


def test_a_customer_cannot_read_the_catalogue(api, outsider):
    api.force_authenticate(outsider)
    assert api.get(reverse("pos-product-list")).status_code == 403


def test_reception_can_read_but_not_write_products(api, receptionist):
    api.force_authenticate(receptionist)
    assert api.get(reverse("pos-product-list")).status_code == 200
    response = api.post(reverse("pos-product-list"), {"name": "x"}, format="json")
    assert response.status_code == 403


def test_reception_cannot_void_a_sale(api, receptionist, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    api.force_authenticate(receptionist)
    response = api.post(
        reverse("pos-sale-void", kwargs={"pk": sale.pk}),
        {"reason": "Not allowed to do this"},
        format="json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def test_product_payload_carries_the_derived_figures(api, manager):
    stocked_product(
        quantity=4, cost_price=Decimal("40.00"), sale_price=Decimal("120.00"),
        tax_rate=Decimal("20.00"),
    )
    api.force_authenticate(manager)
    row = api.get(reverse("pos-product-list")).json()["results"][0]
    assert Decimal(row["net_price"]) == Decimal("100.00")
    assert Decimal(row["margin_amount"]) == Decimal("60.00")
    assert Decimal(row["stock_balance"]) == Decimal("4.00")
    assert Decimal(row["stock_value"]) == Decimal("160.00")


def test_creating_a_product_opens_its_ledger(api, manager):
    category = ProductCategoryFactory()
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-product-list"),
        {
            "sku": "API-01",
            "name": "API wax",
            "category": category.pk,
            "cost_price": "10.00",
            "sale_price": "30.00",
            "tax_rate": "20.00",
            "unit": Product.Unit.PIECE,
            "track_stock": True,
            "low_stock_threshold": 5,
            "opening_stock": "15.00",
        },
        format="json",
    )
    assert response.status_code == 201
    product = Product.objects.get(sku="API-01")
    assert product.stock_quantity == 15


def test_stock_is_not_writable_through_the_product_endpoint(api, manager):
    product = stocked_product(quantity=5)
    api.force_authenticate(manager)
    api.patch(
        reverse("pos-product-detail", kwargs={"pk": product.pk}),
        {"stock_quantity": 999},
        format="json",
    )
    product.refresh_from_db()
    assert product.stock_quantity == 5


def test_adjust_stock_action_appends_a_movement(api, manager):
    product = stocked_product(quantity=5)
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-product-adjust-stock", kwargs={"pk": product.pk}),
        {"quantity": "7", "reason": "Delivery", "movement_type": "purchase"},
        format="json",
    )
    assert response.status_code == 201
    product.refresh_from_db()
    assert product.stock_quantity == 12


def test_adjust_stock_refuses_to_go_negative(api, manager):
    product = stocked_product(quantity=2)
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-product-adjust-stock", kwargs={"pk": product.pk}),
        {"quantity": "-9", "reason": "Typo"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "validation_error"


def test_lookup_resolves_a_barcode(api, manager):
    stocked_product(quantity=5, barcode="8690000000002", name="Zinc")
    api.force_authenticate(manager)
    response = api.get(reverse("pos-product-lookup"), {"code": "8690000000002"})
    assert response.status_code == 200
    assert response.json()["name"] == "Zinc"


def test_lookup_reports_an_unknown_code(api, manager):
    api.force_authenticate(manager)
    response = api.get(reverse("pos-product-lookup"), {"code": "0000000000000"})
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


def test_low_stock_endpoint(api, manager):
    stocked_product(quantity=1, low_stock_threshold=5, name="Nearly out")
    stocked_product(quantity=90, low_stock_threshold=5, name="Plenty")
    api.force_authenticate(manager)
    rows = api.get(reverse("pos-product-low-stock")).json()
    assert [row["name"] for row in rows] == ["Nearly out"]


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
def test_checkout_rings_up_a_sale(api, manager):
    product = stocked_product(quantity=10, sale_price=Decimal("120.00"), tax_rate=Decimal("20.00"))
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-sale-checkout"),
        {
            "items": [{"product": product.pk, "quantity": "2"}],
            "payment_method": "cash",
            "amount_tendered": "300.00",
        },
        format="json",
    )
    assert response.status_code == 201
    payload = response.json()
    assert Decimal(payload["total_amount"]) == Decimal("240.00")
    assert Decimal(payload["change_given"]) == Decimal("60.00")
    product.refresh_from_db()
    assert product.stock_quantity == 8


def test_checkout_refuses_more_than_the_shelf_holds(api, manager):
    product = stocked_product(quantity=1)
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-sale-checkout"),
        {"items": [{"product": product.pk, "quantity": "5"}], "payment_method": "card"},
        format="json",
    )
    assert response.status_code == 400
    assert Sale.objects.count() == 0


def test_checkout_needs_at_least_one_line(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-sale-checkout"), {"items": [], "payment_method": "cash"}, format="json"
    )
    assert response.status_code == 400


def test_void_action_reverses_the_stock(api, manager):
    product = stocked_product(quantity=10)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("3"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("1000.00"))

    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-sale-void", kwargs={"pk": sale.pk}),
        {"reason": "Customer returned everything"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == Sale.Status.VOIDED
    product.refresh_from_db()
    assert product.stock_quantity == 10


def test_void_requires_a_meaningful_reason(api, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-sale-void", kwargs={"pk": sale.pk}), {"reason": "no"}, format="json"
    )
    assert response.status_code == 400


def test_sales_are_not_writable_directly(api, manager):
    api.force_authenticate(manager)
    response = api.post(reverse("pos-sale-list"), {"total_amount": "10.00"}, format="json")
    assert response.status_code == 405


def test_summary_endpoint_reports_takings(api, manager):
    product = stocked_product(quantity=10, sale_price=Decimal("100.00"), tax_rate=Decimal("0.00"))
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("2"))
    services.complete_sale(cart, user=manager, amount_tendered=Decimal("200.00"))

    api.force_authenticate(manager)
    payload = api.get(reverse("pos-sale-summary"), {"range": "30"}).json()
    assert payload["sale_count"] == 1
    assert Decimal(payload["revenue"]) == Decimal("200.00")


# ---------------------------------------------------------------------------
# Stock movements
# ---------------------------------------------------------------------------
def test_the_ledger_is_read_only(api, manager):
    product = stocked_product(quantity=5)
    api.force_authenticate(manager)
    response = api.post(
        reverse("pos-stockmovement-list"),
        {"product": product.pk, "movement_type": "purchase", "quantity": "1"},
        format="json",
    )
    assert response.status_code == 405
    assert StockMovement.objects.filter(product=product).count() == 1
