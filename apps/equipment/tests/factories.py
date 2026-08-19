"""Factories for equipment tests.

Other modules (rentals, maintenance, lessons) may import these to build a fleet
without duplicating the field defaults.
"""

from __future__ import annotations

from decimal import Decimal

import factory
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role
from apps.core.enums import EquipmentCondition, EquipmentStatus, SurfLevel

from ..models import Equipment, EquipmentCategory


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"staff{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.test")
    first_name = "Test"
    last_name = "Staff"
    role = Role.EQUIPMENT_MANAGER
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or "surf-test-password-1")
        self.save(update_fields=["password"])


class EquipmentCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EquipmentCategory
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"category{n}")
    name = factory.Sequence(lambda n: f"Category {n}")
    icon = "package"
    sort_order = 100
    is_active = True


class SoftboardCategoryFactory(EquipmentCategoryFactory):
    code = "softboard"
    name = "Softboard"


class WetsuitCategoryFactory(EquipmentCategoryFactory):
    code = "wetsuit"
    name = "Wetsuit"


class EquipmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Equipment

    category = factory.SubFactory(EquipmentCategoryFactory)
    name = factory.Sequence(lambda n: f"Board {n}")
    brand = "Demo Boards"
    model = "Mod Fun"
    size_label = "8'0\""
    length_cm = Decimal("244.0")
    volume_litres = Decimal("68.00")
    suitable_min_level = SurfLevel.FIRST_TIME
    suitable_max_level = SurfLevel.INTERMEDIATE
    min_rider_weight_kg = Decimal("40.0")
    max_rider_weight_kg = Decimal("110.0")
    purchase_price = Decimal("9500.00")
    current_value = Decimal("7200.00")
    status = EquipmentStatus.AVAILABLE
    condition = EquipmentCondition.GOOD
    storage_location = "Container A"
    is_rentable = False
    is_lesson_stock = True


class RentableEquipmentFactory(EquipmentFactory):
    is_rentable = True
    rental_price_hourly = Decimal("150.00")
    rental_price_daily = Decimal("600.00")
    rental_price_weekly = Decimal("3000.00")
    deposit_amount = Decimal("500.00")


class WetsuitFactory(EquipmentFactory):
    category = factory.SubFactory(WetsuitCategoryFactory)
    name = factory.Sequence(lambda n: f"Wetsuit {n}")
    brand = "Demo Wetsuits"
    model = "Epic"
    size_label = "M"
    wetsuit_thickness = "4/3"
    length_cm = None
    volume_litres = None
