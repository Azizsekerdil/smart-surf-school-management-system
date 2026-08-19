"""Factories for customer test data.

Other modules may import these: ``CustomerFactory`` is the canonical way to
build a bookable person in a test.
"""

from __future__ import annotations

from datetime import date

import factory
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role
from apps.core.enums import BookingSource, Language
from apps.core.models import Tag
from apps.customers.models import Customer, CustomerTag

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"staff{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.test")
    first_name = "Test"
    last_name = factory.Sequence(lambda n: f"Staff{n}")
    role = Role.RECEPTION
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or "surf-school-test-pw")
        self.save(update_fields=["password"])


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tag
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Tag {n}")
    slug = factory.Sequence(lambda n: f"tag-{n}")
    color = "#0ea5e9"


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    first_name = factory.Sequence(lambda n: f"Deniz{n}")
    last_name = factory.Sequence(lambda n: f"Yilmaz{n}")
    email = factory.Sequence(lambda n: f"customer{n}@example.test")
    # 0500 000 ... is not an allocated Turkish mobile prefix, so a fixture
    # value can never collide with a real subscriber.
    phone = factory.Sequence(lambda n: f"+9050000{n:05d}")
    birth_date = date(1995, 6, 15)
    preferred_language = Language.ENGLISH
    source = BookingSource.WALK_IN
    is_active = True


class MinorCustomerFactory(CustomerFactory):
    """A customer under 18 — the stricter rules apply to these."""

    birth_date = factory.LazyFunction(lambda: date(date.today().year - 12, 5, 1))
    emergency_contact_name = "Ayse Yilmaz"
    emergency_contact_phone = "+905000000001"
    emergency_contact_relation = "Mother"


class CustomerTagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomerTag

    customer = factory.SubFactory(CustomerFactory)
    tag = factory.SubFactory(TagFactory)
