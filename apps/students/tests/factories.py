"""Factories for student test data.

Other modules may import these: ``StudentFactory`` builds a teachable person
(customer included), ``SwimmingStudentFactory`` one who may go beyond depth.
"""

from __future__ import annotations

from decimal import Decimal

import factory

from apps.core.enums import SurfLevel
from apps.customers.tests.factories import CustomerFactory, MinorCustomerFactory, UserFactory
from apps.students.models import SkillAssessment, Student

__all__ = [
    "UserFactory",
    "CustomerFactory",
    "StudentFactory",
    "SwimmingStudentFactory",
    "MinorStudentFactory",
    "SkillAssessmentFactory",
]


class StudentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Student

    customer = factory.SubFactory(CustomerFactory)
    surf_level = SurfLevel.FIRST_TIME
    can_swim = False
    weight_kg = Decimal("70.00")
    height_cm = 175
    is_active = True


class SwimmingStudentFactory(StudentFactory):
    """A student with confirmed water competence."""

    can_swim = True
    swim_distance_m = 50
    surf_level = SurfLevel.BEGINNER


class MinorStudentFactory(StudentFactory):
    customer = factory.SubFactory(MinorCustomerFactory)
    weight_kg = Decimal("38.00")
    height_cm = 145


class SkillAssessmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SkillAssessment

    student = factory.SubFactory(StudentFactory)
    level_before = SurfLevel.FIRST_TIME
    level_after = SurfLevel.FIRST_TIME
    paddling = 3
    popup = 3
    positioning = 3
    wave_reading = 3
    safety = 3
