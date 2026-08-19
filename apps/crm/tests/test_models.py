"""Model behaviour and validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.crm.models import Campaign, Interaction, Lead, Segment

from .factories import CampaignFactory, InteractionFactory, LeadFactory, SegmentFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------
def test_lead_str_and_full_name():
    lead = LeadFactory(first_name="Ada", last_name="Deniz")
    assert lead.full_name == "Ada Deniz"
    assert str(lead) == "Ada Deniz"


def test_lead_full_name_without_surname():
    lead = LeadFactory(first_name="Ada", last_name="")
    assert lead.full_name == "Ada"


def test_lead_is_open_only_before_the_outcome():
    assert LeadFactory(status=Lead.Status.QUALIFIED).is_open is True
    assert LeadFactory(status=Lead.Status.LOST, lost_reason="Too expensive").is_open is False


def test_weighted_value_is_exact_decimal_arithmetic():
    lead = LeadFactory(expected_value=Decimal("1000.00"), probability=Decimal("33.33"))
    assert lead.weighted_value == Decimal("333.30")
    assert isinstance(lead.weighted_value, Decimal)


def test_lead_without_any_contact_channel_is_rejected():
    lead = LeadFactory.build(email="", phone="")
    with pytest.raises(ValidationError) as excinfo:
        lead.full_clean()
    assert "email" in excinfo.value.error_dict


def test_lost_lead_must_carry_a_reason():
    lead = LeadFactory(status=Lead.Status.NEW)
    lead.status = Lead.Status.LOST
    with pytest.raises(ValidationError) as excinfo:
        lead.full_clean()
    assert "lost_reason" in excinfo.value.error_dict


def test_won_lead_without_a_customer_is_rejected():
    lead = LeadFactory()
    lead.status = Lead.Status.WON
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_scheduled_action_needs_a_description():
    lead = LeadFactory(next_action="", next_action_at=timezone.now())
    with pytest.raises(ValidationError) as excinfo:
        lead.full_clean()
    assert "next_action" in excinfo.value.error_dict


def test_soft_delete_hides_the_lead_from_the_default_manager():
    lead = LeadFactory()
    lead.delete()
    assert not Lead.objects.filter(pk=lead.pk).exists()
    assert Lead.all_objects.filter(pk=lead.pk).exists()


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------
def test_interaction_requires_a_lead_or_a_customer():
    interaction = InteractionFactory.build(lead=None, customer=None)
    with pytest.raises(ValidationError):
        interaction.full_clean()


def test_interaction_cannot_be_logged_in_the_future():
    interaction = InteractionFactory.build(
        lead=LeadFactory(), occurred_at=timezone.now() + timezone.timedelta(hours=2)
    )
    with pytest.raises(ValidationError) as excinfo:
        interaction.full_clean()
    assert "occurred_at" in excinfo.value.error_dict


def test_follow_up_requires_a_due_date():
    interaction = InteractionFactory.build(
        lead=LeadFactory(), follow_up_required=True, follow_up_at=None
    )
    with pytest.raises(ValidationError) as excinfo:
        interaction.full_clean()
    assert "follow_up_at" in excinfo.value.error_dict


def test_follow_up_cannot_predate_the_interaction():
    now = timezone.now()
    interaction = InteractionFactory.build(
        lead=LeadFactory(),
        occurred_at=now,
        follow_up_required=True,
        follow_up_at=now - timezone.timedelta(days=1),
    )
    with pytest.raises(ValidationError) as excinfo:
        interaction.full_clean()
    assert "follow_up_at" in excinfo.value.error_dict


def test_duration_only_applies_to_timed_interactions():
    interaction = InteractionFactory.build(
        lead=LeadFactory(), kind=Interaction.Kind.EMAIL, duration_minutes=15
    )
    with pytest.raises(ValidationError) as excinfo:
        interaction.full_clean()
    assert "duration_minutes" in excinfo.value.error_dict


def test_overdue_follow_up_is_flagged():
    interaction = InteractionFactory(
        occurred_at=timezone.now() - timezone.timedelta(days=3),
        follow_up_required=True,
        follow_up_at=timezone.now() - timezone.timedelta(days=1),
    )
    assert interaction.is_follow_up_overdue is True


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------
def test_campaign_code_is_generated_when_left_blank():
    campaign = CampaignFactory(code="")
    assert campaign.code.startswith("CMP")
    second = CampaignFactory(code="")
    assert second.code != campaign.code


def test_campaign_roi_and_rates():
    campaign = CampaignFactory(
        actual_spend=Decimal("500.00"),
        revenue_attributed=Decimal("2000.00"),
        sent_count=200,
        opened_count=80,
        converted_count=20,
    )
    assert campaign.roi == Decimal("300.00")
    assert campaign.open_rate == Decimal("40.00")
    assert campaign.conversion_rate == Decimal("10.00")
    assert campaign.cost_per_conversion == Decimal("25.00")


def test_campaign_roi_is_undefined_without_spend():
    campaign = CampaignFactory(actual_spend=Decimal("0.00"))
    assert campaign.roi is None
    assert campaign.conversion_rate is None


def test_campaign_end_date_cannot_precede_start():
    campaign = CampaignFactory.build(
        start_date=timezone.localdate(),
        end_date=timezone.localdate() - timezone.timedelta(days=1),
    )
    with pytest.raises(ValidationError) as excinfo:
        campaign.full_clean()
    assert "end_date" in excinfo.value.error_dict


def test_campaign_cannot_have_more_opens_than_sends():
    campaign = CampaignFactory.build(sent_count=10, opened_count=11)
    with pytest.raises(ValidationError) as excinfo:
        campaign.full_clean()
    assert "opened_count" in excinfo.value.error_dict


def test_scheduled_email_campaign_needs_a_message_and_an_audience():
    campaign = CampaignFactory.build(
        status=Campaign.Status.SCHEDULED,
        channel=Campaign.Channel.EMAIL,
        message_subject="",
        message_body="",
        target_segment=None,
    )
    with pytest.raises(ValidationError) as excinfo:
        campaign.full_clean()
    assert "message_subject" in excinfo.value.error_dict
    assert "target_segment" in excinfo.value.error_dict


def test_allowed_transitions_never_reopen_a_completed_campaign():
    campaign = CampaignFactory(status=Campaign.Status.COMPLETED)
    assert campaign.allowed_next_statuses() == ()


# ---------------------------------------------------------------------------
# Segment
# ---------------------------------------------------------------------------
def test_segment_rejects_an_unknown_criteria_key():
    segment = SegmentFactory.build(criteria={"__import__": "os"})
    with pytest.raises(ValidationError):
        segment.full_clean()


def test_segment_rejects_a_bad_surf_level():
    segment = SegmentFactory.build(criteria={"surf_level": ["not_a_level"]})
    with pytest.raises(ValidationError):
        segment.full_clean()


def test_segment_rejects_contradictory_visit_rules():
    segment = SegmentFactory.build(criteria={"last_visit_days": 30, "no_visit_days": 90})
    with pytest.raises(ValidationError):
        segment.full_clean()


def test_segment_accepts_the_documented_example():
    segment = SegmentFactory.build(
        criteria={
            "surf_level": ["beginner"],
            "last_visit_days": 90,
            "min_bookings": 2,
            "marketing_consent": True,
        }
    )
    segment.full_clean()  # must not raise


def test_segment_describes_its_rules_in_words():
    segment = SegmentFactory(criteria={"min_bookings": 3})
    assert any("3" in line for line in segment.describe_criteria())


def test_segment_resolve_returns_a_queryset():
    segment = SegmentFactory(criteria={"has_email": True})
    assert segment.resolve().count() >= 0


def test_segment_without_a_calculation_is_stale():
    assert Segment(name="x", last_calculated_at=None).is_stale is True
