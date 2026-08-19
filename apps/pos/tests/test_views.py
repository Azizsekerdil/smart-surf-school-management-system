from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.pos import services
from apps.pos.models import Product, Sale, StockMovement

from .factories import ProductCategoryFactory, ProductFactory, stocked_product

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="shopmanager", email="manager@example.test", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def receptionist(db):
    """Reception may sell but may not reprice or void."""
    return User.objects.create_user(
        username="reception", email="reception@example.test", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def outsider(db):
    return User.objects.create_user(
        username="guest", email="guest@example.test", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_terminal_requires_authentication(client):
    response = client.get(reverse("pos:terminal"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_customer_cannot_reach_the_till(client, outsider):
    client.force_login(outsider)
    assert client.get(reverse("pos:terminal")).status_code == 403


def test_reception_can_sell_but_not_change_the_catalogue(client, receptionist):
    client.force_login(receptionist)
    assert client.get(reverse("pos:terminal")).status_code == 200
    assert client.get(reverse("pos:product_create")).status_code == 403
    assert client.get(reverse("pos:stock_adjust")).status_code == 403


def test_reception_cannot_void_a_sale(client, receptionist, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    client.force_login(receptionist)
    assert client.get(reverse("pos:void", kwargs={"pk": sale.pk})).status_code == 403


def test_manager_reaches_every_screen(client, manager):
    stocked_product(quantity=5)
    client.force_login(manager)
    for name in ("pos:terminal", "pos:list", "pos:product_list", "pos:category_list",
                 "pos:movement_list", "pos:stock_adjust", "pos:product_create"):
        assert client.get(reverse(name)).status_code == 200, name


# ---------------------------------------------------------------------------
# The till
# ---------------------------------------------------------------------------
def test_terminal_lists_sellable_products(client, manager):
    product = stocked_product(quantity=5, name="Cold water wax")
    client.force_login(manager)
    response = client.get(reverse("pos:terminal"))
    assert response.status_code == 200
    assert product.name.encode() in response.content


def test_adding_by_product_id_returns_the_cart(client, manager):
    product = stocked_product(quantity=5, name="Leash 6ft")
    client.force_login(manager)
    response = client.post(reverse("pos:cart_add"), {"product": product.pk})
    assert response.status_code == 200
    assert b"Leash 6ft" in response.content


def test_adding_by_barcode_resolves_the_product(client, manager):
    product = stocked_product(quantity=5, barcode="8690000000001", name="Zinc stick")
    client.force_login(manager)
    response = client.post(reverse("pos:cart_add"), {"code": "8690000000001"})
    assert response.status_code == 200
    assert b"Zinc stick" in response.content
    assert product.pk in services.SessionCart(client.session).product_ids()


def test_an_unknown_barcode_reports_it_without_breaking_the_cart(client, manager):
    client.force_login(manager)
    response = client.post(reverse("pos:cart_add"), {"code": "0000000000000"})
    assert response.status_code == 200
    assert b"No product matches" in response.content


def test_adding_more_than_the_shelf_holds_is_refused(client, manager):
    product = stocked_product(quantity=1)
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    response = client.post(reverse("pos:cart_add"), {"product": product.pk})
    assert response.status_code == 200
    assert b"left in stock" in response.content
    assert services.SessionCart(client.session).lines()[0].quantity == Decimal("1.00")


def test_the_quantity_stepper_updates_the_line(client, manager):
    product = stocked_product(quantity=10)
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    client.post(reverse("pos:cart_update"), {"product": product.pk, "delta": "2"})
    assert services.SessionCart(client.session).lines()[0].quantity == Decimal("3.00")


def test_removing_a_line_empties_the_cart(client, manager):
    product = stocked_product(quantity=10)
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    client.post(reverse("pos:cart_remove"), {"product": product.pk})
    assert services.SessionCart(client.session).is_empty


def test_a_whole_sale_discount_is_applied(client, manager):
    product = stocked_product(quantity=10, sale_price=Decimal("100.00"), tax_rate=Decimal("0.00"))
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    client.post(reverse("pos:cart_discount"), {"percent": "10"})
    totals = services.SessionCart(client.session).totals()
    assert totals.total_amount == Decimal("90.00")


def test_checkout_creates_the_sale_and_moves_the_stock(client, manager):
    product = stocked_product(quantity=10, sale_price=Decimal("120.00"))
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    response = client.post(
        reverse("pos:checkout"),
        {"payment_method": "cash", "amount_tendered": "200.00"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    sale = Sale.objects.get()
    assert sale.total_amount == Decimal("120.00")
    assert sale.change_given == Decimal("80.00")
    product.refresh_from_db()
    assert product.stock_quantity == 9
    assert services.SessionCart(client.session).is_empty


def test_checkout_without_htmx_redirects_to_the_receipt(client, manager):
    product = stocked_product(quantity=10)
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    response = client.post(reverse("pos:checkout"), {"payment_method": "cash"})
    sale = Sale.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("pos:receipt", kwargs={"pk": sale.pk})


def test_checkout_refuses_short_cash(client, manager):
    product = stocked_product(quantity=10, sale_price=Decimal("120.00"))
    client.force_login(manager)
    client.post(reverse("pos:cart_add"), {"product": product.pk})
    response = client.post(
        reverse("pos:checkout"),
        {"payment_method": "cash", "amount_tendered": "10.00"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert Sale.objects.count() == 0


def test_product_grid_filters_by_search(client, manager):
    stocked_product(quantity=5, name="Cold water wax")
    stocked_product(quantity=5, name="Board bag")
    client.force_login(manager)
    response = client.get(reverse("pos:product_grid"), {"q": "wax"})
    assert b"Cold water wax" in response.content
    assert b"Board bag" not in response.content


# ---------------------------------------------------------------------------
# Catalogue & stock
# ---------------------------------------------------------------------------
def test_creating_a_product_opens_its_ledger(client, manager):
    category = ProductCategoryFactory()
    client.force_login(manager)
    response = client.post(
        reverse("pos:product_create"),
        {
            "sku": "WAX-01",
            "barcode": "",
            "name": "Cold water wax",
            "description": "",
            "category": category.pk,
            "cost_price": "20.00",
            "sale_price": "60.00",
            "tax_rate": "20.00",
            "unit": Product.Unit.PIECE,
            "track_stock": "on",
            "low_stock_threshold": "5",
            "supplier": "",
            "is_active": "on",
            "sort_order": "100",
            "opening_stock": "24",
        },
    )
    assert response.status_code == 302
    product = Product.objects.get(sku="WAX-01")
    assert product.stock_quantity == 24
    assert StockMovement.objects.filter(
        product=product, movement_type=StockMovement.MovementType.INITIAL
    ).count() == 1


def test_a_shelf_price_below_cost_is_rejected(client, manager):
    category = ProductCategoryFactory()
    client.force_login(manager)
    response = client.post(
        reverse("pos:product_create"),
        {
            "sku": "BAD-01", "barcode": "", "name": "Loss leader", "description": "",
            "category": category.pk, "cost_price": "100.00", "sale_price": "10.00",
            "tax_rate": "0.00", "unit": Product.Unit.PIECE, "track_stock": "on",
            "low_stock_threshold": "5", "supplier": "", "is_active": "on",
            "sort_order": "100", "opening_stock": "0",
        },
    )
    assert response.status_code == 200
    assert Product.objects.filter(sku="BAD-01").count() == 0


def test_stock_adjustment_appends_to_the_ledger(client, manager):
    product = stocked_product(quantity=5)
    client.force_login(manager)
    response = client.post(
        reverse("pos:stock_adjust"),
        {
            "product": product.pk,
            "movement_type": StockMovement.MovementType.PURCHASE,
            "direction": "in",
            "quantity": "12",
            "reference": "INV-9001",
            "reason": "Delivery from the supplier",
        },
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.stock_quantity == 17


def test_a_removal_larger_than_the_shelf_is_rejected(client, manager):
    product = stocked_product(quantity=2)
    client.force_login(manager)
    response = client.post(
        reverse("pos:stock_adjust"),
        {
            "product": product.pk,
            "movement_type": StockMovement.MovementType.DAMAGE,
            "direction": "out",
            "quantity": "9",
            "reason": "Crushed in the van",
        },
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.stock_quantity == 2


# ---------------------------------------------------------------------------
# Sales screens
# ---------------------------------------------------------------------------
def test_sale_detail_and_receipt_render(client, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    client.force_login(manager)
    assert client.get(reverse("pos:detail", kwargs={"pk": sale.pk})).status_code == 200
    receipt = client.get(reverse("pos:receipt", kwargs={"pk": sale.pk}))
    assert receipt.status_code == 200
    assert sale.sale_number.encode() in receipt.content


def test_voiding_through_the_view_restores_the_stock(client, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("2"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    client.force_login(manager)
    response = client.post(
        reverse("pos:void", kwargs={"pk": sale.pk}),
        {"reason": "Rang up the wrong customer"},
    )
    assert response.status_code == 302
    sale.refresh_from_db()
    assert sale.status == Sale.Status.VOIDED
    product.refresh_from_db()
    assert product.stock_quantity == 5


def test_sale_list_shows_the_receipt(client, manager):
    product = stocked_product(quantity=5)
    cart = services.SessionCart.detached()
    cart.add(product, Decimal("1"))
    sale = services.complete_sale(cart, user=manager, amount_tendered=Decimal("500.00"))

    client.force_login(manager)
    response = client.get(reverse("pos:list"))
    assert response.status_code == 200
    assert sale.sale_number.encode() in response.content


def test_product_list_flags_low_stock(client, manager):
    stocked_product(quantity=1, low_stock_threshold=5, name="Almost gone")
    client.force_login(manager)
    response = client.get(reverse("pos:product_list"))
    assert response.status_code == 200
    assert b"Almost gone" in response.content


def test_product_list_search_narrows_the_table(client, manager):
    ProductFactory(name="Board bag")
    stocked_product(quantity=5, name="Cold water wax")
    client.force_login(manager)
    response = client.get(reverse("pos:product_list"), {"q": "wax"})
    assert b"Cold water wax" in response.content
    assert b"Board bag" not in response.content
