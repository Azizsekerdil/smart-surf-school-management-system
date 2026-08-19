"""factory-boy factories for the lessons module.

Instructors, spots and students belong to other apps, so those factories are
resolved lazily by app label: this keeps the lessons tests runnable as soon as
those models exist, without importing their test modules.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import factory
from django.apps import apps as django_apps
from django.utils import timezone

from apps.core.enums import LessonStatus, SurfLevel

from ..models import Lesson, LessonAttendance, LessonType


class LessonTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LessonType
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"LT{n:03d}")
    name = factory.Sequence(lambda n: f"Group lesson {n}")
    category = LessonType.Category.GROUP
    min_level = SurfLevel.FIRST_TIME
    max_level = SurfLevel.BEGINNER
    duration_minutes = 120
    min_students = 1
    max_students = 8
    base_price = Decimal("750.00")
    price_per_extra_student = Decimal("500.00")
    colour = "#0ea5e9"
    is_active = True


class SurfSpotFactory(factory.django.DjangoModelFactory):
    """Minimal spot. Field names are filled in by the locations app's own
    defaults; only ``name`` is assumed."""

    class Meta:
        model = "locations.SurfSpot"
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Test Break {n}")


class InstructorFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "instructors.Instructor"


class StudentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "students.Student"


class LessonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Lesson

    lesson_type = factory.SubFactory(LessonTypeFactory)
    spot = factory.SubFactory(SurfSpotFactory)
    instructor = factory.SubFactory(InstructorFactory)
    date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=1))
    start_time = time(10, 0)
    end_time = time(12, 0)
    capacity = 6
    status = LessonStatus.SCHEDULED

    @factory.post_generation
    def assistants(self, create, extracted, **kwargs):
        if create and extracted:
            self.assistant_instructors.set(extracted)


class LessonAttendanceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LessonAttendance

    lesson = factory.SubFactory(LessonFactory)
    student = factory.SubFactory(StudentFactory)
    status = LessonAttendance.Status.REGISTERED


def model_available(app_label: str, model_name: str) -> bool:
    """True when another app's model is installed — used to skip integration tests."""
    try:
        django_apps.get_model(app_label, model_name)
    except LookupError:
        return False
    return True
