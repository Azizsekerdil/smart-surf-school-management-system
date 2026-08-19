"""Factories for the booking tests.

Bookings sit on top of four sibling apps (customers, students, lessons, surf
camps). Rather than hard-coding every column those apps might grow, the
``build_*`` helpers set the fields bookings actually depends on and let
:func:`fill_required` invent a valid value for anything else the sibling model
insists on. That keeps this file stable while the rest of the project is still
being written, without ever faking a value bookings itself cares about.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from datetime import time as dt_time
from decimal import Decimal

import factory
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.bookings.models import Booking, WaitlistEntry
from apps.core.enums import BookingSource, BookingStatus, SurfLevel


# ---------------------------------------------------------------------------
# Generic helpers for sibling-app models
# ---------------------------------------------------------------------------
def _placeholder(field, index: int):
    """A valid value for a required field we have no opinion about."""
    if field.choices:
        return field.choices[0][0]
    if isinstance(field, models.EmailField):
        return f"person{index}@example.test"
    if isinstance(field, (models.CharField, models.TextField, models.SlugField)):
        value = f"test-{index}"
        max_length = getattr(field, "max_length", None)
        return value[:max_length] if max_length else value
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, models.DecimalField):
        return Decimal("0.00")
    if isinstance(field, (models.IntegerField, models.FloatField)):
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


_counter = {"n": 0}


class _NotGiven:
    """Sentinel separating "argument omitted" from "argument passed as None".

    ``build_lesson(instructor=None)`` is a deliberate request for a lesson with
    no instructor attached; omitting the argument just means "any coach".
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<not given>"


_NOT_GIVEN = _NotGiven()


def fill_required(model, values: dict) -> dict:
    """Complete *values* with placeholders for every other required field."""
    _counter["n"] += 1
    index = _counter["n"]
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
        placeholder = _placeholder(field, index)
        if placeholder is not None:
            complete[field.name] = placeholder
    return complete


def _create(app_label: str, model_name: str, **values):
    model = django_apps.get_model(app_label, model_name)
    known = {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}
    values = {key: value for key, value in values.items() if key in known}
    return model.objects.create(**fill_required(model, values))


# ---------------------------------------------------------------------------
# Sibling-app builders
# ---------------------------------------------------------------------------
def build_customer(**values):
    values.setdefault("first_name", "Deniz")
    values.setdefault("last_name", f"Customer{_counter['n']}")
    values.setdefault("email", f"customer{_counter['n']}@example.test")
    return _create("customers", "Customer", **values)


def build_student(customer=None, level=SurfLevel.BEGINNER, age: int = 30, **values):
    """A student attached to a customer.

    ``students.Student`` is a one-to-one profile over ``customers.Customer``:
    the relation is required and unique, so each student needs its own
    customer. Age lives on the customer as ``birth_date`` — the age rules that
    tighten instructor ratios for minors read it from there.
    """
    born = timezone.localdate().replace(year=timezone.localdate().year - age)
    if customer is None:
        customer = build_customer(birth_date=born)
    elif getattr(customer, "birth_date", None) is None:
        customer.birth_date = born
        customer.save(update_fields=["birth_date"])

    # Student is a OneToOne over Customer: reusing a customer must return the
    # existing profile rather than violate the unique constraint.
    existing = django_apps.get_model("students", "Student").objects.filter(
        customer=customer
    ).first()
    if existing is not None:
        if getattr(existing, "surf_level", None) != level:
            existing.surf_level = level
            existing.save(update_fields=["surf_level"])
        return existing

    values.setdefault("customer", customer)
    values.setdefault("surf_level", level)
    return _create("students", "Student", **values)


def build_instructor(with_availability: bool = True, **values):
    """An instructor who can actually be scheduled.

    Two required pieces that ``fill_required`` cannot invent:
    ``Instructor.user`` is a non-null relation, and an instructor with no
    published ``AvailabilitySlot`` is refused by the scheduling rules — so a
    bare instructor makes every booking test fail for the wrong reason.
    """
    if "user" not in values:
        User = get_user_model()
        _counter["n"] += 1
        index = _counter["n"]
        user = User.objects.create_user(
            username=f"coach{index}",
            email=f"coach{index}@example.test",
            password="surf-school-test-pw",
            first_name="Coach",
            last_name=f"Number{index}",
            role="surf_instructor",
        )
        values["user"] = user

    instructor = _create("instructors", "Instructor", **values)

    if with_availability:
        slot_model = django_apps.get_model("instructors", "AvailabilitySlot")
        for weekday in range(7):
            slot_model.objects.get_or_create(
                instructor=instructor,
                weekday=weekday,
                start_time=dt_time(8, 0),
                defaults={"end_time": dt_time(19, 0), "is_active": True},
            )
    return instructor


def build_lesson_type(
    *,
    name: str = "Beginner group",
    color: str = "#0ea5e9",
    min_level: str = SurfLevel.FIRST_TIME,
    max_level: str = SurfLevel.INTERMEDIATE,
    **values,
):
    _counter["n"] += 1
    values.setdefault("code", f"LT{_counter['n']:04d}")
    values.setdefault("name", name)
    # lessons.LessonType spells this "colour"; "color" is accepted for callers
    # written against the other spelling.
    values.setdefault("colour", color)
    values.setdefault("min_level", min_level)
    values.setdefault("max_level", max_level)
    values.setdefault("duration_minutes", 120)
    values.setdefault("max_students", 8)
    values.setdefault("base_price", Decimal("50.00"))
    return _create("lessons", "LessonType", **values)


def build_spot(**values):
    """A surf spot — lessons.Lesson.spot is a required relation."""
    _counter["n"] += 1
    index = _counter["n"]
    values.setdefault("name", f"Test Break {index}")
    values.setdefault("slug", f"test-break-{index}")
    values.setdefault("code", f"TB{index:04d}")
    values.setdefault("latitude", 38.28)
    values.setdefault("longitude", 26.37)
    values.setdefault("beach_facing_deg", 200.0)
    values.setdefault("capacity", 40)
    return _create("locations", "SurfSpot", **values)


def build_lesson(
    *,
    start: datetime | None = None,
    duration_minutes: int = 120,
    max_students: int = 8,
    lesson_type=None,
    instructor=_NOT_GIVEN,
    price: Decimal = Decimal("50.00"),
    **values,
):
    """A lesson that starts in the future by default.

    ``lessons.Lesson`` stores the day and the clock times separately —
    ``date`` plus ``start_time``/``end_time`` as ``TimeField`` — and requires a
    ``spot``. The caller still passes a single ``start`` datetime, which is
    split here.
    """
    start = start or timezone.now() + timedelta(days=3)
    start = timezone.localtime(start) if timezone.is_aware(start) else start
    finish = start + timedelta(minutes=duration_minutes)

    values.setdefault("lesson_type", lesson_type or build_lesson_type())
    values.setdefault("spot", build_spot())
    # Lesson.instructor is a non-null relation, so a lesson genuinely cannot be
    # stored without one. Passing instructor=None explicitly therefore means
    # "give me the in-memory shape the conflict checker has to defend against":
    # the row is saved with a real coach and the attribute detached afterwards.
    # Omitting the argument entirely just means "any coach will do".
    detach_instructor = instructor is None
    if instructor is _NOT_GIVEN or instructor is None:
        instructor = build_instructor()
    values.setdefault("instructor", instructor)
    values.setdefault("date", start.date())
    values.setdefault("start_time", start.time().replace(microsecond=0))
    values.setdefault("end_time", finish.time().replace(microsecond=0))
    values.setdefault("capacity", max_students)
    values.setdefault("price_override", price)
    lesson = _create("lessons", "Lesson", **values)
    if detach_instructor:
        lesson.instructor = None
    return lesson


def build_camp(*, start: date | None = None, days: int = 7, capacity: int = 12, **values):
    _counter["n"] += 1
    start = start or timezone.localdate() + timedelta(days=14)
    values.setdefault("code", f"CAMP{_counter['n']:04d}")
    values.setdefault("name", f"Test Camp {_counter['n']}")
    values.setdefault("spot", build_spot())
    values.setdefault("start_date", start)
    values.setdefault("end_date", start + timedelta(days=days))
    values.setdefault("capacity", capacity)
    values.setdefault("price", Decimal("600.00"))
    return _create("surf_camps", "SurfCamp", **values)


# ---------------------------------------------------------------------------
# Booking factories
# ---------------------------------------------------------------------------
class BookingFactory(factory.django.DjangoModelFactory):
    """A pending lesson booking with a customer, a student and a lesson."""

    class Meta:
        model = Booking
        skip_postgeneration_save = True

    booking_type = Booking.BookingType.LESSON
    customer = factory.LazyFunction(build_customer)
    student = factory.LazyAttribute(lambda obj: build_student(customer=obj.customer))
    lesson = factory.LazyFunction(build_lesson)
    surf_camp = None
    status = BookingStatus.PENDING
    participants = 1
    unit_price = Decimal("50.00")
    discount_amount = Decimal("0.00")
    paid_amount = Decimal("0.00")
    source = BookingSource.WALK_IN

    @factory.post_generation
    def totals(self, create, extracted, **kwargs):  # noqa: ARG002 - factory hook
        if create:
            self.recalculate_totals(commit=True)


class CampBookingFactory(BookingFactory):
    booking_type = Booking.BookingType.CAMP
    lesson = None
    surf_camp = factory.LazyFunction(build_camp)
    unit_price = Decimal("600.00")


class WaitlistEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WaitlistEntry
        skip_postgeneration_save = True

    customer = factory.LazyFunction(build_customer)
    student = factory.LazyAttribute(lambda obj: build_student(customer=obj.customer))
    lesson = factory.LazyFunction(build_lesson)
    surf_camp = None
    participants = 1
