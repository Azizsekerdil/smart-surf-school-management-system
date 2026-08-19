"""Fixtures for the analytics tests.

Analytics reads from a dozen other apps, so these builders create the *minimum*
real rows each metric needs, filling anything else the sibling model insists on
with a valid placeholder. That keeps the tests stable while sibling schemas are
still settling, without ever faking a value analytics itself measures — every
amount, date and capacity a test asserts on is set explicitly here.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import factory
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.analytics.models import MetricSnapshot
from apps.core.enums import (
    BookingSource,
    BookingStatus,
    EquipmentCondition,
    EquipmentStatus,
    LessonStatus,
    SurfLevel,
)

_counter = {"n": 0}


def _next() -> int:
    _counter["n"] += 1
    return _counter["n"]


def _placeholder(field, index: int):
    """A valid value for a required field this test has no opinion about."""
    if getattr(field, "choices", None):
        return field.choices[0][0]
    if isinstance(field, models.EmailField):
        return f"person{index}@example.test"
    if isinstance(field, models.SlugField):
        return f"slug-{index}"
    if isinstance(field, (models.CharField, models.TextField)):
        value = f"test-{index}"
        max_length = getattr(field, "max_length", None)
        return value[:max_length] if max_length else value
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, models.DecimalField):
        return Decimal("0.00")
    if isinstance(field, models.FloatField):
        return 1.0
    if isinstance(field, models.IntegerField):
        return 1
    if isinstance(field, models.DateTimeField):
        return timezone.now()
    if isinstance(field, models.DateField):
        return timezone.localdate()
    if isinstance(field, models.TimeField):
        return time(9, 0)
    if isinstance(field, models.DurationField):
        return timedelta(hours=1)
    if isinstance(field, models.JSONField):
        return {}
    return None


def fill_required(model, values: dict) -> dict:
    """Complete *values* with placeholders for every other required field."""
    index = _next()
    complete = dict(values)
    for field in model._meta.get_fields():
        if not getattr(field, "concrete", False) or field.primary_key:
            continue
        if field.name in complete or field.auto_created:
            continue
        if field.null or field.blank or field.has_default():
            continue
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            continue  # a required relation must be passed in explicitly
        value = _placeholder(field, index)
        if value is not None:
            complete[field.name] = value
    return complete


def create(app_label: str, model_name: str, **values):
    """Create a sibling-app row, ignoring keys that model does not have."""
    model = django_apps.get_model(app_label, model_name)
    known = {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}
    values = {key: value for key, value in values.items() if key in known}
    return model.objects.create(**fill_required(model, values))


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
def build_user(**values):
    index = _next()
    values.setdefault("username", f"user{index}")
    values.setdefault("email", f"user{index}@example.test")
    return get_user_model().objects.create_user(password="pw-test-12345", **values)


def build_customer(**values):
    index = _next()
    values.setdefault("first_name", "Deniz")
    values.setdefault("last_name", f"Customer{index}")
    values.setdefault("email", f"customer{index}@example.test")
    return create("customers", "Customer", **values)


def build_student(customer=None, level: str = SurfLevel.BEGINNER, **values):
    values.setdefault("customer", customer or build_customer())
    values.setdefault("surf_level", level)
    return create("students", "Student", **values)


def build_instructor(**values):
    values.setdefault("user", build_user())
    return create("instructors", "Instructor", **values)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def build_spot(**values):
    index = _next()
    values.setdefault("name", f"Test Break {index}")
    values.setdefault("latitude", 36.8)
    values.setdefault("longitude", 30.6)
    return create("locations", "SurfSpot", **values)


def build_lesson_type(name: str = "Beginner group", **values):
    index = _next()
    values.setdefault("code", f"lt-{index}")
    values.setdefault("name", name)
    values.setdefault("duration_minutes", 120)
    values.setdefault("max_students", 8)
    values.setdefault("base_price", Decimal("50.00"))
    return create("lessons", "LessonType", **values)


def build_lesson(
    *,
    on_date=None,
    hour: int = 10,
    capacity: int = 8,
    status: str = LessonStatus.COMPLETED,
    lesson_type=None,
    instructor=None,
    spot=None,
    **values,
):
    values.setdefault("lesson_type", lesson_type or build_lesson_type())
    values.setdefault("spot", spot or build_spot())
    values.setdefault("instructor", instructor or build_instructor())
    values.setdefault("date", on_date or timezone.localdate())
    values.setdefault("start_time", time(hour, 0))
    values.setdefault("end_time", time(min(23, hour + 2), 0))
    values.setdefault("capacity", capacity)
    values.setdefault("status", status)
    return create("lessons", "Lesson", **values)


#: Sentinel so ``student=None`` can mean "no student" rather than "make one".
AUTO = object()


def build_booking(
    *,
    customer=None,
    student=AUTO,
    lesson=None,
    booked_at=None,
    status: str = BookingStatus.COMPLETED,
    participants: int = 1,
    paid: Decimal = Decimal("50.00"),
    source: str = BookingSource.WALK_IN,
    **values,
):
    customer = customer or build_customer()
    booking = create(
        "bookings",
        "Booking",
        customer=customer,
        student=build_student(customer=customer) if student is AUTO else student,
        lesson=lesson,
        status=status,
        participants=participants,
        unit_price=paid,
        total_amount=paid * participants,
        paid_amount=paid * participants,
        source=source,
        **values,
    )
    if booked_at is not None:
        # ``booked_at`` has a default, so it must be forced after creation.
        type(booking).objects.filter(pk=booking.pk).update(booked_at=booked_at)
        booking.refresh_from_db()
    return booking


# ---------------------------------------------------------------------------
# Equipment & rentals
# ---------------------------------------------------------------------------
def build_equipment_category(**values):
    index = _next()
    values.setdefault("code", f"cat-{index}")
    values.setdefault("name", f"Category {index}")
    return create("equipment", "EquipmentCategory", **values)


def build_equipment(*, rentable: bool = True, category=None, **values):
    index = _next()
    values.setdefault("category", category or build_equipment_category())
    values.setdefault("name", f"Board {index}")
    values.setdefault("is_rentable", rentable)
    values.setdefault("status", EquipmentStatus.AVAILABLE)
    values.setdefault("condition", EquipmentCondition.GOOD)
    values.setdefault("rental_price_daily", Decimal("30.00"))
    return create("equipment", "Equipment", **values)


def build_rental(
    *,
    customer=None,
    start_at=None,
    hours: int = 4,
    returned: bool = True,
    total: Decimal = Decimal("40.00"),
    **values,
):
    start_at = start_at or timezone.now() - timedelta(hours=hours)
    values.setdefault("customer", customer or build_customer())
    values.setdefault("start_at", start_at)
    values.setdefault("expected_return_at", start_at + timedelta(hours=hours))
    values.setdefault("returned_at", start_at + timedelta(hours=hours) if returned else None)
    values.setdefault("subtotal", total)
    values.setdefault("total_amount", total)
    values.setdefault("paid_amount", total)
    return create("rentals", "Rental", **values)


def build_payment(*, customer=None, amount=Decimal("100.00"), paid_at=None, **values):
    """A finance-ledger payment. A refund is the same row with a negative amount."""
    values.setdefault("customer", customer or build_customer())
    values.setdefault("amount", amount)
    values.setdefault("paid_at", paid_at or timezone.now())
    return create("finance", "Payment", **values)


def build_rental_item(rental, equipment, *, quantity: int = 1, price=Decimal("40.00")):
    return create(
        "rentals",
        "RentalItem",
        rental=rental,
        equipment=equipment,
        quantity=quantity,
        unit_price=price,
        line_total=price * quantity,
    )


# ---------------------------------------------------------------------------
# Analytics' own model
# ---------------------------------------------------------------------------
class MetricSnapshotFactory(DjangoModelFactory):
    class Meta:
        model = MetricSnapshot

    metric_key = factory.Sequence(lambda n: f"revenue.total.{n}")
    period_start = factory.LazyFunction(lambda: timezone.localdate() - timedelta(days=1))
    period_end = factory.LazyFunction(timezone.localdate)
    granularity = MetricSnapshot.Granularity.DAY
    value = Decimal("1250.0000")
    count = 12
    dimensions = factory.Dict({})
