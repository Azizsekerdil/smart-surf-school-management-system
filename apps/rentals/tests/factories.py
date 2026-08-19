"""Object factories for the rental tests.

``customers.Customer``, ``students.Student`` and ``equipment.Equipment`` belong
to other modules and their exact field sets are not this app's business, so
:func:`make_instance` builds a minimally valid row by introspecting the target
model and filling in whatever the database insists on. Overrides that the target
model does not have are ignored, which keeps these tests stable while the
neighbouring modules evolve.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import time, timedelta
from decimal import Decimal

import factory
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.core.enums import EquipmentCondition, EquipmentStatus, RentalPeriod
from apps.rentals.models import Rental, RentalItem

_counter = itertools.count(1)


# ---------------------------------------------------------------------------
# Generic minimal-instance builder
# ---------------------------------------------------------------------------
def _field_names(model) -> set[str]:
    names: set[str] = set()
    for field in model._meta.fields:
        names.add(field.name)
        names.add(field.attname)
    return names


def _needs_value(field) -> bool:
    if field.primary_key or isinstance(field, models.AutoField):
        return False
    if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
        return False
    if field.null or field.has_default():
        return False
    if field.unique:
        return True
    if field.empty_strings_allowed and field.blank:
        return False
    return True


def _value_for(field):
    index = next(_counter)
    if field.choices:
        return list(field.choices)[0][0]
    if isinstance(field, models.ForeignKey):
        return make_instance(field.related_model)
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, models.DecimalField):
        return Decimal("10.00")
    if isinstance(field, models.FloatField):
        return 1.0
    if isinstance(field, models.DateTimeField):
        return timezone.now()
    if isinstance(field, models.DateField):
        return timezone.localdate()
    if isinstance(field, models.TimeField):
        return time(9, 0)
    if isinstance(field, models.DurationField):
        return timedelta(hours=1)
    if isinstance(field, models.UUIDField):
        return uuid.uuid4()
    if isinstance(field, models.JSONField):
        return {}
    if isinstance(field, models.IntegerField):
        return index
    if isinstance(field, models.EmailField):
        return f"person{index}@example.test"
    value = f"{field.name}-{index}"
    max_length = getattr(field, "max_length", None)
    return value[:max_length] if max_length else value


def make_instance(model, **overrides):
    """Create one row of *model*, filling required fields automatically."""
    known = _field_names(model)
    payload = {key: value for key, value in overrides.items() if key in known}
    for field in model._meta.fields:
        if field.name in payload or field.attname in payload:
            continue
        if not _needs_value(field):
            continue
        payload[field.name] = _value_for(field)
    return model._default_manager.create(**payload)


# ---------------------------------------------------------------------------
# Cross-app helpers
# ---------------------------------------------------------------------------
def make_customer(**overrides):
    return make_instance(apps.get_model("customers", "Customer"), **overrides)


def make_student(**overrides):
    return make_instance(apps.get_model("students", "Student"), **overrides)


def make_equipment(**overrides):
    """A rentable asset with all three rates configured."""
    index = next(_counter)
    defaults = {
        "asset_code": f"AST-{index:05d}",
        "status": EquipmentStatus.AVAILABLE,
        "condition": EquipmentCondition.GOOD,
        # equipment.Equipment names these rental_price_*; the short names do
        # not exist on the model, so setting them produced zero-priced hires.
        "rental_price_hourly": Decimal("10.00"),
        "rental_price_daily": Decimal("40.00"),
        "rental_price_weekly": Decimal("200.00"),
    }
    defaults.update(overrides)
    return make_instance(apps.get_model("equipment", "Equipment"), **defaults)


def make_user(role: str = "rental_staff", **overrides):
    User = get_user_model()
    index = next(_counter)
    payload = {
        "username": overrides.pop("username", f"staff{index}"),
        "email": overrides.pop("email", f"staff{index}@example.test"),
        "role": role,
    }
    payload.update(overrides)
    password = payload.pop("password", "surf-school-test-pw")
    user = User(**payload)
    user.set_password(password)
    user.save()
    user.raw_password = password
    return user


# ---------------------------------------------------------------------------
# Rental factories
# ---------------------------------------------------------------------------
class RentalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Rental

    customer = factory.LazyFunction(make_customer)
    period_type = RentalPeriod.DAILY
    status = Rental.Status.ACTIVE
    start_at = factory.LazyFunction(lambda: timezone.now() - timedelta(hours=2))
    expected_return_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    deposit_amount = Decimal("100.00")


class RentalItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RentalItem

    rental = factory.SubFactory(RentalFactory)
    equipment = factory.LazyFunction(make_equipment)
    unit_price = Decimal("40.00")
    quantity = 1
    condition_out = EquipmentCondition.GOOD
