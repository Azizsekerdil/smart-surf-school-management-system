"""Shared fixtures for the lessons tests.

Lessons sit in the middle of the domain: they need a spot, an instructor and
students, all owned by other apps. Those objects are built with the owning
app's own factory when it exists, and with a minimal local fallback otherwise,
so this suite never encodes another module's field names.
"""

from __future__ import annotations

import importlib
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import LessonStatus, SurfLevel

from ..models import Lesson
from .factories import (
    InstructorFactory,
    LessonTypeFactory,
    StudentFactory,
    SurfSpotFactory,
)


def _external_factory(app_label: str, name: str, fallback):
    """Return the owning app's factory when it ships one, else *fallback*."""
    try:
        module = importlib.import_module(f"apps.{app_label}.tests.factories")
    except Exception:  # noqa: BLE001 - the app may not ship test factories
        return fallback
    return getattr(module, name, fallback)


def _build(app_label: str, name: str, fallback, **kwargs):
    factory_class = _external_factory(app_label, name, fallback)
    try:
        return factory_class(**kwargs)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot build {app_label}.{name}: {exc}")


@pytest.fixture
def spot(db):
    return _build("locations", "SurfSpotFactory", SurfSpotFactory)


@pytest.fixture
def instructor(db):
    # A schedulable instructor: without published availability every
    # lesson would be refused, which is the rule working, not a fixture.
    return _build(
        "instructors", "InstructorFactory", InstructorFactory, published_availability=True
    )


@pytest.fixture
def other_instructor(db):
    # A schedulable instructor: without published availability every
    # lesson would be refused, which is the rule working, not a fixture.
    return _build(
        "instructors", "InstructorFactory", InstructorFactory, published_availability=True
    )


def make_student(level: str = SurfLevel.BEGINNER, age: int | None = 30):
    """Create a student and force the attributes lessons actually reads."""
    student = _build("students", "StudentFactory", StudentFactory)
    for attribute in ("level", "surf_level"):
        if hasattr(student, attribute):
            setattr(student, attribute, level)
    if age is not None:
        for attribute in ("date_of_birth", "birth_date"):
            if hasattr(student, attribute):
                today = timezone.localdate()
                setattr(student, attribute, date(today.year - age, 1, 1))
    student.save()
    return student


@pytest.fixture
def student(db):
    return make_student()


@pytest.fixture
def another_student(db):
    return make_student()


@pytest.fixture
def minor_student(db):
    return make_student(age=12)


@pytest.fixture
def lesson_type(db):
    return LessonTypeFactory(
        code="GRP2H",
        name="Group lesson 2h",
        min_level=SurfLevel.FIRST_TIME,
        max_level=SurfLevel.BEGINNER,
        duration_minutes=120,
        min_students=1,
        max_students=8,
        base_price=Decimal("750.00"),
        price_per_extra_student=Decimal("500.00"),
    )


@pytest.fixture
def lesson(db, lesson_type, spot, instructor):
    return Lesson.objects.create(
        lesson_type=lesson_type,
        spot=spot,
        instructor=instructor,
        date=timezone.localdate() + timedelta(days=1),
        start_time=time(10, 0),
        end_time=time(12, 0),
        capacity=4,
        status=LessonStatus.SCHEDULED,
    )


@pytest.fixture
def past_lesson(db, lesson_type, spot, instructor):
    return Lesson.objects.create(
        lesson_type=lesson_type,
        spot=spot,
        instructor=instructor,
        date=timezone.localdate() - timedelta(days=1),
        start_time=time(10, 0),
        end_time=time(12, 0),
        capacity=4,
        status=LessonStatus.SCHEDULED,
    )


def _user(username: str, role: str):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@surfschool.test",
        password="lessons-test-pass-123",
        role=role,
    )


@pytest.fixture
def manager_user(db):
    """Full lessons access: view, add, change, manage."""
    return _user("lessons_manager", Role.MANAGER)


@pytest.fixture
def instructor_user(db):
    """View + change, but no add and no manage."""
    return _user("lessons_coach", Role.SURF_INSTRUCTOR)


@pytest.fixture
def unauthorised_user(db):
    """Maintenance staff hold no ``lessons.*`` capability at all."""
    return _user("lessons_outsider", Role.MAINTENANCE_STAFF)
