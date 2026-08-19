"""Factories for shop test data.

Other modules may import these: ``ProductFactory`` is the canonical way to build
something sellable, and ``stocked_product`` gives it an opening balance through
the ledger rather than by writing ``stock_quantity`` behind the service's back.
"""

from __future__ import annotations

from decimal import Decimal

import factory
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role
from apps.core.enums import PaymentMethod
from apps.pos.models import Product, ProductCategory, Sale, StockMovement

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)
        # The password hook saves the row itself; the extra save factory-boy
        # would add is redundant (and deprecated).
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"till{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.test")
    first_name = "Till"
    last_name = factory.Sequence(lambda n: f"Operator{n}")
    role = Role.RECEPTION
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or "surf-school-test-pw")
        self.save(update_fields=["password"])


class ProductCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductCategory
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"category-{n}")
    name = factory.Sequence(lambda n: f"Category {n}")
    icon = "package"
    sort_order = 100
    is_active = True


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    sku = factory.Sequence(lambda n: f"SKU-{n:04d}")
    barcode = ""
    name = factory.Sequence(lambda n: f"Surf wax {n}")
    category = factory.SubFactory(ProductCategoryFactory)
    cost_price = Decimal("40.00")
    sale_price = Decimal("120.00")
    tax_rate = Decimal("20.00")
    low_stock_threshold = 5
    track_stock = True
    unit = Product.Unit.PIECE
    is_active = True
    sort_order = 100


class SaleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Sale

    payment_method = PaymentMethod.CASH
    status = Sale.Status.COMPLETED


def stocked_product(quantity: int = 10, **kwargs) -> Product:
    """A product with *quantity* on the shelf, opened through the ledger."""
    from apps.pos import services

    product = ProductFactory(**kwargs)
    services.set_opening_stock(product, Decimal(quantity))
    services.recalculate_stock(product)
    return product


def movement_count(product: Product) -> int:
    return StockMovement.objects.filter(product=product).count()
