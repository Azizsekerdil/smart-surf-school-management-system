"""Factories for maintenance tests.

Equipment belongs to a sibling module, so :func:`make_equipment` prefers that
module's own factory and otherwise builds a valid instance by introspecting the
model. The maintenance suite therefore keeps working whichever way the
equipment app chooses to name its fields.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal

import factory
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import (
    DamageType,
    EquipmentCondition,
    EquipmentStatus,
    GenericStatus,
    Severity,
)

from ..models import MaintenanceRecord, MaintenanceSchedule

_counter = itertools.count(1)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"staff{n}")
    email = factory.Sequence(lambda n: f"staff{n}@surfschool.test")
    first_name = "Deniz"
    last_name = factory.Sequence(lambda n: f"Yilmaz{n}")
    role = Role.MAINTENANCE_STAFF
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        if create:
            obj.set_password(extracted or "surf-school-test-pw")
            obj.save(update_fields=["password"])


# ---------------------------------------------------------------------------
# Equipment — built through the sibling module when it offers a factory
# ---------------------------------------------------------------------------
_SKIPPED_FIELD_TYPES = (models.AutoField, models.BigAutoField, models.SmallAutoField)


def _sample_value(field, index: int):
    """A valid, minimal value for a required model field."""
    if field.choices:
        return field.choices[0][0]
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, (models.DecimalField,)):
        return Decimal("1.00")
    if isinstance(field, (models.IntegerField, models.FloatField)):
        return 1
    if isinstance(field, models.DateTimeField):
        return timezone.now()
    if isinstance(field, models.DateField):
        return timezone.localdate()
    if isinstance(field, models.TimeField):
        return timezone.localtime().time()
    if isinstance(field, models.EmailField):
        return f"item{index}@surfschool.test"
    if isinstance(field, models.UUIDField):
        import uuid

        return uuid.uuid4()
    if isinstance(field, (models.JSONField,)):
        return {}
    max_length = getattr(field, "max_length", None) or 40
    value = f"{field.name[:8]}-{index}"
    return value[:max_length]


def _create_minimal(model, overrides: dict | None = None, depth: int = 0):
    """Create *model* filling every required field with a plausible value."""
    overrides = dict(overrides or {})
    index = next(_counter)
    data: dict = {}

    for field in model._meta.get_fields():
        if not getattr(field, "concrete", False) or isinstance(field, _SKIPPED_FIELD_TYPES):
            continue
        if field.name in overrides:
            continue
        if field.has_default() or field.blank or field.null or field.auto_created:
            continue
        if isinstance(field, (models.FileField, models.ImageField)):
            continue
        if field.is_relation:
            if depth >= 3:
                continue
            data[field.name] = _create_minimal(field.related_model, depth=depth + 1)
            continue
        data[field.name] = _sample_value(field, index)

    data.update(overrides)
    return model.objects.create(**data)


def make_equipment(**overrides):
    """Return a saved ``equipment.Equipment`` usable in maintenance tests."""
    try:
        from apps.equipment.tests.factories import EquipmentFactory  # type: ignore
    except (ImportError, AttributeError):
        EquipmentFactory = None

    model = django_apps.get_model("equipment", "Equipment")
    defaults: dict = {}
    field_names = {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}
    if "status" in field_names:
        defaults["status"] = EquipmentStatus.AVAILABLE
    if "condition" in field_names:
        defaults["condition"] = EquipmentCondition.GOOD
    if "purchase_date" in field_names:
        defaults["purchase_date"] = timezone.localdate() - timedelta(days=400)
    defaults.update(overrides)

    if EquipmentFactory is not None:
        return EquipmentFactory(**defaults)
    return _create_minimal(model, overrides=defaults)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
class MaintenanceRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MaintenanceRecord

    equipment = factory.LazyFunction(make_equipment)
    damage_type = DamageType.DING
    severity = Severity.MEDIUM
    status = GenericStatus.OPEN
    description = "Small ding on the deck near the front foot."
    reported_at = factory.LazyFunction(timezone.now)
    made_unusable = True


class MaintenanceScheduleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MaintenanceSchedule

    equipment = factory.LazyFunction(make_equipment)
    interval_days = 90
    check_items = factory.LazyFunction(
        lambda: ["Inspect leash plug", "Check fin boxes", "Look for delamination"]
    )
    is_active = True
