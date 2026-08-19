"""Factories for surf camp objects.

Related objects owned by other modules are referenced by import path so the
factory is only resolved when a test actually needs one.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.core.enums import SurfLevel

from ..models import ActivityType, CampActivity, CampDay, CampParticipant, CampStatus, SurfCamp


class SurfCampFactory(DjangoModelFactory):
    class Meta:
        model = SurfCamp

    name = factory.Sequence(lambda n: f"Surf Camp {n}")
    description = "Seven nights of surfing, yoga and video analysis."
    start_date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=14))
    end_date = factory.LazyAttribute(lambda obj: obj.start_date + timedelta(days=6))
    spot = factory.SubFactory("apps.locations.tests.factories.SurfSpotFactory")
    capacity = 10
    min_participants = 4
    min_level = SurfLevel.FIRST_TIME
    max_level = SurfLevel.INTERMEDIATE
    price = Decimal("850.00")
    deposit_amount = Decimal("200.00")
    single_room_supplement = Decimal("150.00")
    includes_accommodation = True
    includes_meals = True
    includes_transfer = True
    includes_equipment = True
    accommodation_name = "Surf House"
    transfer_pickup_point = "Airport, terminal 1"
    status = CampStatus.PUBLISHED
    is_active = True


class CampParticipantFactory(DjangoModelFactory):
    class Meta:
        model = CampParticipant

    camp = factory.SubFactory(SurfCampFactory)
    student = factory.SubFactory("apps.students.tests.factories.StudentFactory")
    room_type = CampParticipant.RoomType.SHARED
    amount_paid = Decimal("0.00")


class CampDayFactory(DjangoModelFactory):
    class Meta:
        model = CampDay

    camp = factory.SubFactory(SurfCampFactory)
    date = factory.LazyAttribute(lambda obj: obj.camp.start_date)
    day_number = 1
    title = "Arrival day"


class CampActivityFactory(DjangoModelFactory):
    class Meta:
        model = CampActivity

    camp_day = factory.SubFactory(CampDayFactory)
    start_time = time(9, 30)
    end_time = time(11, 30)
    title = "Morning surf lesson"
    activity_type = ActivityType.SURF_LESSON
