"""Factories for the finance tests.

Sibling apps (customers, instructors, lessons, bookings, rentals, equipment)
are built through their own factories where those exist, so a schema change
next door surfaces as one failing import rather than as silently wrong money.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from apps.core.enums import BookingStatus, PaymentMethod
from apps.customers.tests.factories import CustomerFactory, UserFactory  # noqa: F401
from apps.finance.models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    Payment,
    PricePackage,
)
from apps.instructors.tests.factories import InstructorFactory  # noqa: F401
from apps.lessons.tests.factories import LessonTypeFactory
from apps.locations.tests.factories import SurfSpotFactory


# ---------------------------------------------------------------------------
# Sibling-app builders
# ---------------------------------------------------------------------------
def build_lesson(*, instructor=None, on_date=None, status=None, **values):
    """A lesson with everything the finance module reads from it."""
    from apps.core.enums import LessonStatus
    from apps.lessons.models import Lesson

    values.setdefault("lesson_type", LessonTypeFactory())
    values.setdefault("spot", SurfSpotFactory())
    values.setdefault("instructor", instructor or InstructorFactory())
    values.setdefault("date", on_date or timezone.localdate())
    values.setdefault("start_time", time(10, 0))
    values.setdefault("end_time", time(12, 0))
    values.setdefault("capacity", 6)
    values.setdefault("status", status or LessonStatus.COMPLETED)
    return Lesson.objects.create(**values)


def build_booking(
    *,
    customer=None,
    lesson=None,
    unit_price=Decimal("750.00"),
    participants=1,
    status=BookingStatus.CONFIRMED,
    **values,
):
    """A confirmed booking with its totals already calculated."""
    from apps.bookings.models import Booking

    booking = Booking.objects.create(
        booking_type=Booking.BookingType.LESSON if lesson else Booking.BookingType.PACKAGE,
        customer=customer or CustomerFactory(),
        lesson=lesson,
        status=status,
        participants=participants,
        unit_price=Decimal(unit_price),
        **values,
    )
    booking.recalculate_totals(commit=True)
    return booking


def build_rental(
    *,
    customer=None,
    subtotal=Decimal("300.00"),
    late_fee=Decimal("0.00"),
    damage_fee=Decimal("0.00"),
    **values,
):
    """A returned hire contract carrying a real charge.

    The hire always gets a line: ``Rental.recalculate_totals`` derives the
    subtotal and the damage charge from the items, so a contract with no items
    is worth nothing however its columns were seeded.
    """
    from apps.rentals.models import Rental, RentalItem
    from apps.rentals.tests.factories import make_equipment

    now = timezone.now()
    rental = Rental.objects.create(
        customer=customer or CustomerFactory(),
        status=Rental.Status.RETURNED,
        start_at=now - timedelta(days=1),
        expected_return_at=now,
        returned_at=now,
        late_fee=Decimal(late_fee),
        **values,
    )
    RentalItem.objects.create(
        rental=rental,
        equipment=make_equipment(),
        unit_price=Decimal(subtotal),
        quantity=1,
        line_total=Decimal(subtotal),
        damage_charge=Decimal(damage_fee),
        damage_reported=Decimal(damage_fee) > Decimal("0.00"),
    )
    rental.recalculate_totals(save=True)
    return rental


# ---------------------------------------------------------------------------
# Finance factories
# ---------------------------------------------------------------------------
class ExpenseCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExpenseCategory
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"CAT{n:03d}")
    name = factory.Sequence(lambda n: f"Category {n}")
    is_active = True
    sort_order = 0


class ExpenseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Expense

    category = factory.SubFactory(ExpenseCategoryFactory)
    description = "Wetsuit repair kit"
    amount = Decimal("100.00")
    tax_amount = Decimal("20.00")
    spent_on = factory.LazyFunction(timezone.localdate)
    supplier = "Coastal Supplies"
    is_recurring = False


class InvoiceFactory(factory.django.DjangoModelFactory):
    """A draft invoice with no lines — add them, then ``recalculate()``."""

    class Meta:
        model = Invoice

    customer = factory.SubFactory(CustomerFactory)
    issue_date = factory.LazyFunction(timezone.localdate)
    due_date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=14))
    status = Invoice.Status.DRAFT
    tax_rate = Decimal("0.00")


class InvoiceLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InvoiceLine

    invoice = factory.SubFactory(InvoiceFactory)
    description = "Group lesson"
    quantity = Decimal("1.00")
    unit_price = Decimal("750.00")
    discount_amount = Decimal("0.00")
    sort_order = 0


class PaymentFactory(factory.django.DjangoModelFactory):
    """A settled cash payment. Prefer ``services.record_payment`` in tests that
    care about balances — this is for read-side fixtures only."""

    class Meta:
        model = Payment

    customer = factory.SubFactory(CustomerFactory)
    amount = Decimal("750.00")
    method = PaymentMethod.CASH
    category = Payment.Category.LESSON
    paid_at = factory.LazyFunction(timezone.now)


class CommissionRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CommissionRecord

    instructor = factory.SubFactory(InstructorFactory)
    period_start = factory.LazyFunction(lambda: timezone.localdate() - timedelta(days=30))
    period_end = factory.LazyFunction(timezone.localdate)
    base_amount = Decimal("1000.00")
    commission_percent = Decimal("10.00")
    commission_amount = Decimal("100.00")
    status = CommissionRecord.Status.PENDING


class PricePackageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PricePackage
        django_get_or_create = ("code",)

    name = factory.Sequence(lambda n: f"Package {n}")
    code = factory.Sequence(lambda n: f"PKG{n:03d}")
    description = "Five group lessons, taken whenever suits you."
    lesson_count = 5
    price = Decimal("3000.00")
    validity_days = 180
    is_active = True
    sort_order = 0


class CustomerPackageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CustomerPackage

    customer = factory.SubFactory(CustomerFactory)
    package = factory.SubFactory(PricePackageFactory)
    purchased_on = factory.LazyFunction(timezone.localdate)
    expires_on = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=180))
    lessons_total = 5
    lessons_used = 0
    amount_paid = Decimal("3000.00")
    status = CustomerPackage.Status.ACTIVE
