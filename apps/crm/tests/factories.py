"""factory-boy factories for the CRM.

Other modules may import these to build CRM fixtures without knowing the
model internals.
"""

from __future__ import annotations

from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import BookingSource
from apps.crm.models import Campaign, Interaction, Lead, Segment


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"crm_user_{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.test")
    first_name = "Deniz"
    last_name = factory.Sequence(lambda n: f"Kaya{n}")
    role = Role.MARKETING
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):  # noqa: N805
        if create:
            obj.set_password(extracted or "surf-school-test-pw")
            obj.save(update_fields=["password"])


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Lead

    first_name = factory.Sequence(lambda n: f"Lead{n}")
    last_name = "Yilmaz"
    email = factory.Sequence(lambda n: f"lead{n}@example.test")
    phone = "+90 500 000 00 00"
    source = BookingSource.WEBSITE
    interest = "Two-hour beginner group lesson in August."
    status = Lead.Status.NEW
    expected_value = Decimal("1200.00")
    probability = Decimal("40.00")


class InteractionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Interaction

    kind = Interaction.Kind.CALL
    direction = Interaction.Direction.OUTBOUND
    subject = "Called about August dates"
    body = "Wants a weekend slot for two people."
    lead = factory.SubFactory(LeadFactory)
    occurred_at = factory.LazyFunction(timezone.now)
    duration_minutes = 8


class SegmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Segment

    name = factory.Sequence(lambda n: f"Segment {n}")
    description = "Customers worth talking to."
    criteria = factory.LazyFunction(lambda: {"has_email": True})
    is_dynamic = True


class CampaignFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Campaign

    name = factory.Sequence(lambda n: f"Campaign {n}")
    channel = Campaign.Channel.EMAIL
    status = Campaign.Status.DRAFT
    start_date = factory.LazyFunction(timezone.localdate)
    end_date = factory.LazyFunction(
        lambda: timezone.localdate() + timezone.timedelta(days=14)
    )
    budget = Decimal("2000.00")
    actual_spend = Decimal("0.00")
    message_subject = "Ride the September swell"
    message_body = "Book three lessons and get the fourth free."
