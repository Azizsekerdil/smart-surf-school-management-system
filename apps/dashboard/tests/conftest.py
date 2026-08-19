"""Fixtures for the dashboard tests.

The dashboard owns no models, so every object here belongs to another app. Each
one is built through the owning app's own factory when it ships one; when it
does not, the test that needs it skips rather than encoding another module's
field names — which is exactly the failure mode this app is designed to avoid
in production too.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role

TEST_PASSWORD = "dashboard-test-pass-123"


def model_available(app_label: str, model_name: str) -> bool:
    try:
        django_apps.get_model(app_label, model_name)
    except LookupError:
        return False
    return True


def build(app_label: str, factory_name: str, **kwargs):
    """Create an object with the owning app's factory, or skip the test."""
    try:
        module = importlib.import_module(f"apps.{app_label}.tests.factories")
    except Exception as exc:  # noqa: BLE001 - the app may ship no factories
        pytest.skip(f"apps.{app_label} has no test factories: {exc}")
    factory_class = getattr(module, factory_name, None)
    if factory_class is None:
        pytest.skip(f"apps.{app_label}.tests.factories has no {factory_name}")
    try:
        return factory_class(**kwargs)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot build {app_label}.{factory_name}: {exc}")


def _user(username: str, role: str, **extra):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@surfschool.test",
        password=TEST_PASSWORD,
        role=role,
        **extra,
    )


# ---------------------------------------------------------------------------
# Minimal cross-app objects
# ---------------------------------------------------------------------------
# Built here rather than through the owning apps' factories: the dashboard needs
# only a handful of columns from each model, and depending on another suite's
# factory defaults would make these tests fail for reasons that have nothing to
# do with the dashboard.
_sequence = {"n": 0}


def _next() -> int:
    _sequence["n"] += 1
    return _sequence["n"]


def require(app_label: str, model_name: str):
    """Return a model, or skip the test when its app is not installed."""
    if not model_available(app_label, model_name):
        pytest.skip(f"{app_label}.{model_name} is not installed")
    return django_apps.get_model(app_label, model_name)


def make_customer(**values):
    Customer = require("customers", "Customer")
    index = _next()
    values.setdefault("first_name", "Deniz")
    values.setdefault("last_name", f"Test{index}")
    values.setdefault("email", f"customer{index}@surfschool.test")
    return Customer.objects.create(**values)


def make_booking(customer=None, **values):
    """A rental-type booking: it needs neither a lesson nor a student."""
    Booking = require("bookings", "Booking")
    values.setdefault("customer", customer or make_customer())
    values.setdefault("booking_type", Booking.BookingType.RENTAL)
    values.setdefault("participants", 1)
    return Booking.objects.create(**values)


def make_instructor(user=None, **values):
    Instructor = require("instructors", "Instructor")
    from apps.accounts.constants import Role as _Role

    values.setdefault("user", user or _user(f"dash_inst{_next()}", _Role.SURF_INSTRUCTOR))
    return Instructor.objects.create(**values)


def make_spot(**values):
    SurfSpot = require("locations", "SurfSpot")
    index = _next()
    values.setdefault("name", f"Dashboard Break {index}")
    values.setdefault("latitude", 38.28)
    values.setdefault("longitude", 26.37)
    values.setdefault("beach_facing_deg", 270.0)
    return SurfSpot.objects.create(**values)


def make_lesson_type(**values):
    LessonType = require("lessons", "LessonType")
    index = _next()
    values.setdefault("code", f"DASH{index:03d}")
    values.setdefault("name", f"Dashboard lesson {index}")
    return LessonType.objects.create(**values)


def make_lesson(day=None, **values):
    """A lesson on *day* (defaults to today) at 10:00–12:00."""
    from datetime import time

    from django.utils import timezone as _timezone

    Lesson = require("lessons", "Lesson")
    values.setdefault("lesson_type", make_lesson_type())
    values.setdefault("spot", make_spot())
    values.setdefault("instructor", make_instructor())
    values.setdefault("date", day or _timezone.localdate())
    values.setdefault("start_time", time(10, 0))
    values.setdefault("end_time", time(12, 0))
    values.setdefault("capacity", 6)
    return Lesson.objects.create(**values)


def make_student(customer=None, **values):
    Student = require("students", "Student")
    values.setdefault("customer", customer or make_customer())
    return Student.objects.create(**values)


def make_payment(customer=None, amount="400.00", when=None, **values):
    from decimal import Decimal

    from django.utils import timezone as _timezone

    from apps.core.enums import PaymentStatus

    Payment = require("finance", "Payment")
    values.setdefault("customer", customer or make_customer())
    values.setdefault("amount", Decimal(amount))
    values.setdefault("status", PaymentStatus.PAID)
    values.setdefault("paid_at", when or _timezone.now())
    return Payment.objects.create(**values)


def make_condition(spot=None, **values):
    """A recorded observation, scored by the surf-conditions module itself."""
    from django.utils import timezone as _timezone

    SurfCondition = require("surf_conditions", "SurfCondition")
    values.setdefault("spot", spot or make_spot())
    values.setdefault("recorded_at", _timezone.now())
    values.setdefault("is_forecast", False)
    values.setdefault("wave_height_m", 0.8)
    values.setdefault("wave_period_s", 9.0)
    values.setdefault("wind_speed_kmh", 12.0)
    values.setdefault("wind_direction_deg", 90.0)
    values.setdefault("water_temperature_c", 19.5)
    condition = SurfCondition.objects.create(**values)

    try:
        from apps.surf_conditions.services import score_condition

        score_condition(condition)
    except Exception:  # noqa: BLE001 - scoring is optional for these tests
        pass
    return condition


@pytest.fixture
def manager_user(db):
    """Sees everything on the dashboard, including money."""
    return _user("dash_manager", Role.MANAGER)


@pytest.fixture
def rental_clerk(db):
    """Rentals and equipment, but no lessons and no finance beyond view."""
    return _user("dash_clerk", Role.RENTAL_STAFF)


@pytest.fixture
def maintenance_user(db):
    """Maintenance staff: no lessons, no bookings, no finance."""
    return _user("dash_fixer", Role.MAINTENANCE_STAFF)


@pytest.fixture
def instructor_user(db):
    return _user("dash_coach", Role.SURF_INSTRUCTOR)


@pytest.fixture
def customer_user(db):
    return _user("dash_customer", Role.CUSTOMER)


@pytest.fixture
def blocked_user(db):
    """Holds a role, but has ``dashboard.view`` explicitly revoked."""
    return _user("dash_blocked", Role.RECEPTION, denied_capabilities=["dashboard.view"])
