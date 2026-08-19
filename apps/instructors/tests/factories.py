"""factory-boy factories for the instructor domain.

Other apps (lessons, bookings, safety) build instructors through these, so the
defaults are deliberately "fully qualified and bookable" — a test that needs an
unqualified coach opts out explicitly.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import SurfLevel

from ..models import (
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"coach{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@surfschool.test")
    first_name = factory.Sequence(lambda n: f"Coach{n}")
    last_name = "Aydın"
    role = Role.SURF_INSTRUCTOR
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):  # noqa: N805
        if not create:
            return
        obj.set_password(extracted or "surf-test-password-123")
        obj.save(update_fields=["password"])


class InstructorFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Instructor

    user = factory.SubFactory(UserFactory)
    bio = "Patient with first-timers, strong on ocean awareness."
    specialties = factory.LazyFunction(lambda: ["beginner", "kids"])
    languages = factory.LazyFunction(lambda: ["tr", "en"])
    max_level_taught = SurfLevel.INTERMEDIATE
    max_students_per_lesson = 8
    hourly_rate = Decimal("450.00")
    commission_percent = Decimal("10.00")
    hire_date = factory.LazyFunction(lambda: timezone.localdate() - dt.timedelta(days=365))
    is_active = True
    is_available_for_booking = True
    emergency_contact_name = "Deniz Aydın"
    emergency_contact_phone = "+90 500 000 00 03"

    @factory.post_generation
    def published_availability(self, create, extracted, **kwargs):
        """Publish a full working week unless a test asks otherwise.

        Scheduling refuses an instructor with no published availability — which
        is correct, but it means a bare InstructorFactory cannot teach anything.
        Availability is therefore OPT-IN: the instructors app's own tests
        exercise the availability rules and need a blank sheet, while modules
        that just need somebody who can teach pass
        ``published_availability=True``.
        """
        if not create or not extracted:
            return
        for weekday in range(7):
            AvailabilitySlot.objects.get_or_create(
                instructor=self,
                weekday=weekday,
                start_time=dt.time(8, 0),
                defaults={"end_time": dt.time(19, 0), "is_active": True},
            )


class CertificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Certification

    instructor = factory.SubFactory(InstructorFactory)
    kind = Certification.Kind.ISA_L1
    name = "ISA Surf Level 1 Instructor"
    issuing_body = "International Surfing Association"
    certificate_number = factory.Sequence(lambda n: f"ISA-{n:05d}")
    issued_on = factory.LazyFunction(lambda: timezone.localdate() - dt.timedelta(days=200))
    expires_on = factory.LazyFunction(lambda: timezone.localdate() + dt.timedelta(days=400))
    is_verified = True


class AvailabilitySlotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AvailabilitySlot

    instructor = factory.SubFactory(InstructorFactory)
    weekday = AvailabilitySlot.Weekday.MONDAY
    start_time = dt.time(9, 0)
    end_time = dt.time(17, 0)
    is_active = True


class TimeOffFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TimeOff

    instructor = factory.SubFactory(InstructorFactory)
    start_date = factory.LazyFunction(lambda: timezone.localdate() + dt.timedelta(days=7))
    end_date = factory.LazyFunction(lambda: timezone.localdate() + dt.timedelta(days=10))
    reason = TimeOff.Reason.HOLIDAY
    is_approved = False


class PerformanceReviewFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PerformanceReview

    instructor = factory.SubFactory(InstructorFactory)
    reviewer = factory.SubFactory(UserFactory, role=Role.HEAD_INSTRUCTOR)
    period_start = factory.LazyFunction(lambda: timezone.localdate() - dt.timedelta(days=90))
    period_end = factory.LazyFunction(lambda: timezone.localdate() - dt.timedelta(days=1))
    teaching_quality = 4
    punctuality = 4
    safety = 5
    communication = 4
    teamwork = 3


def fully_certified(instructor: Instructor) -> Instructor:
    """Give *instructor* one current certification from every required group."""
    today = timezone.localdate()
    for kind, name in (
        (Certification.Kind.ISA_L2, "ISA Surf Level 2 Coach"),
        (Certification.Kind.LIFEGUARD, "Surf Lifeguard Award"),
        (Certification.Kind.FIRST_AID, "Emergency First Aid at Work"),
    ):
        CertificationFactory(
            instructor=instructor,
            kind=kind,
            name=name,
            issued_on=today - dt.timedelta(days=100),
            expires_on=today + dt.timedelta(days=500),
            is_verified=True,
        )
    return instructor
