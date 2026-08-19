"""Business rules — the part a surf school actually depends on."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.crm.models import Campaign, Interaction, Lead
from apps.crm.services import (
    advance_lead_status,
    campaign_performance,
    complete_follow_up,
    convert_lead_to_customer,
    customer_retention_stats,
    log_interaction,
    preview_segment_size,
    resolve_segment,
    set_campaign_status,
)

from .factories import (
    CampaignFactory,
    InteractionFactory,
    LeadFactory,
    SegmentFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def test_advance_moves_the_lead_and_records_the_change():
    lead = LeadFactory(status=Lead.Status.NEW)
    user = UserFactory()
    advance_lead_status(lead, Lead.Status.QUALIFIED, user=user)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.QUALIFIED
    assert lead.updated_by == user


def test_advance_refuses_an_unknown_stage():
    lead = LeadFactory()
    with pytest.raises(ValidationError):
        advance_lead_status(lead, "on_the_beach")


def test_advance_cannot_win_a_lead_directly():
    lead = LeadFactory(status=Lead.Status.PROPOSAL_SENT)
    with pytest.raises(ValidationError):
        advance_lead_status(lead, Lead.Status.WON)


def test_losing_a_lead_requires_a_reason():
    lead = LeadFactory(status=Lead.Status.CONTACTED)
    with pytest.raises(ValidationError):
        advance_lead_status(lead, Lead.Status.LOST)


def test_losing_a_lead_zeroes_the_probability_and_clears_the_action():
    lead = LeadFactory(
        status=Lead.Status.CONTACTED,
        next_action="Call back",
        next_action_at=timezone.now(),
    )
    advance_lead_status(lead, Lead.Status.LOST, lost_reason="Booked elsewhere")
    lead.refresh_from_db()
    assert lead.probability == Decimal("0.00")
    assert lead.next_action == ""
    assert lead.next_action_at is None


def test_reopening_a_lost_lead_clears_the_loss():
    lead = LeadFactory(status=Lead.Status.NEW)
    advance_lead_status(lead, Lead.Status.LOST, lost_reason="No budget")
    advance_lead_status(lead, Lead.Status.CONTACTED)
    lead.refresh_from_db()
    assert lead.lost_reason == ""
    assert lead.probability > Decimal("0.00")


def test_advance_to_the_same_stage_is_a_no_op():
    lead = LeadFactory(status=Lead.Status.NEW)
    assert advance_lead_status(lead, Lead.Status.NEW).status == Lead.Status.NEW


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def test_conversion_creates_a_customer_and_moves_the_history():
    lead = LeadFactory(status=Lead.Status.PROPOSAL_SENT)
    InteractionFactory(lead=lead, customer=None)
    user = UserFactory()

    customer = convert_lead_to_customer(lead, user=user)

    lead.refresh_from_db()
    assert lead.status == Lead.Status.WON
    assert lead.converted_customer_id == customer.pk
    assert lead.converted_at is not None
    assert lead.probability == Decimal("100.00")
    assert lead.interactions.filter(customer=customer).count() == 1


def test_conversion_reuses_an_existing_customer_with_the_same_email():
    first = LeadFactory(email="same@example.test")
    customer = convert_lead_to_customer(first)

    second = LeadFactory(email="SAME@example.test", phone="")
    reused = convert_lead_to_customer(second)

    assert reused.pk == customer.pk


def test_a_lead_cannot_be_converted_twice():
    lead = LeadFactory()
    convert_lead_to_customer(lead)
    with pytest.raises(ValidationError):
        convert_lead_to_customer(lead)


def test_a_lost_lead_cannot_be_converted():
    lead = LeadFactory(status=Lead.Status.NEW)
    advance_lead_status(lead, Lead.Status.LOST, lost_reason="Gone quiet")
    with pytest.raises(ValidationError):
        convert_lead_to_customer(lead)


def test_a_lead_without_contact_details_cannot_be_converted():
    lead = LeadFactory()
    Lead.objects.filter(pk=lead.pk).update(email="", phone="")
    lead.refresh_from_db()
    with pytest.raises(ValidationError):
        convert_lead_to_customer(lead)


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
def test_log_interaction_stamps_the_handler_and_the_time():
    lead = LeadFactory()
    user = UserFactory()
    interaction = log_interaction(
        kind=Interaction.Kind.CALL, subject="Rang about wetsuit sizes", lead=lead, user=user
    )
    assert interaction.handled_by == user
    assert interaction.occurred_at is not None


def test_a_complaint_always_creates_a_follow_up():
    lead = LeadFactory()
    interaction = log_interaction(
        kind=Interaction.Kind.COMPLAINT,
        subject="Board was damaged",
        lead=lead,
        follow_up_required=False,
    )
    assert interaction.follow_up_required is True
    assert interaction.follow_up_at is not None
    assert interaction.sentiment == Interaction.Sentiment.NEGATIVE


def test_negative_sentiment_also_forces_a_follow_up():
    interaction = log_interaction(
        kind=Interaction.Kind.REVIEW,
        subject="Two stars",
        lead=LeadFactory(),
        sentiment=Interaction.Sentiment.NEGATIVE,
    )
    assert interaction.follow_up_required is True


def test_logging_contact_moves_a_new_lead_to_contacted():
    lead = LeadFactory(status=Lead.Status.NEW)
    log_interaction(kind=Interaction.Kind.CALL, subject="First call", lead=lead)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.CONTACTED


def test_an_internal_note_does_not_move_the_lead():
    lead = LeadFactory(status=Lead.Status.NEW)
    log_interaction(kind=Interaction.Kind.NOTE, subject="Rang, no answer", lead=lead)
    lead.refresh_from_db()
    assert lead.status == Lead.Status.NEW


def test_log_interaction_needs_somebody_to_attach_to():
    with pytest.raises(ValidationError):
        log_interaction(kind=Interaction.Kind.CALL, subject="Nobody")


def test_completing_a_follow_up_clears_the_queue_entry():
    interaction = InteractionFactory(
        follow_up_required=True,
        follow_up_at=timezone.now() + timezone.timedelta(days=1),
    )
    complete_follow_up(interaction, user=UserFactory())
    interaction.refresh_from_db()
    assert interaction.follow_up_required is False


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
def test_resolve_segment_caches_the_count():
    segment = SegmentFactory(criteria={"has_email": True})
    resolve_segment(segment)
    segment.refresh_from_db()
    assert segment.last_calculated_at is not None
    assert segment.cached_count >= 0


def test_preview_ignores_an_unknown_rule_rather_than_widening_the_audience():
    # An unrecognised key must never silently become "match everyone".
    assert preview_segment_size({"pk__gt": 0}) == preview_segment_size({})


def test_criteria_are_never_passed_to_filter_verbatim():
    segment = SegmentFactory(criteria={"created_at__year": 2026})
    assert segment.criteria_issues()  # reported, not executed
    segment.resolve().count()  # must not raise


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
def test_campaign_status_follows_the_allowed_transitions():
    campaign = CampaignFactory(status=Campaign.Status.DRAFT, target_segment=SegmentFactory())
    set_campaign_status(campaign, Campaign.Status.SCHEDULED)
    set_campaign_status(campaign, Campaign.Status.RUNNING)
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.RUNNING


def test_a_completed_campaign_cannot_be_restarted():
    campaign = CampaignFactory(status=Campaign.Status.RUNNING, target_segment=SegmentFactory())
    set_campaign_status(campaign, Campaign.Status.COMPLETED)
    with pytest.raises(ValidationError):
        set_campaign_status(campaign, Campaign.Status.RUNNING)


def test_a_campaign_that_already_ended_cannot_be_started():
    campaign = CampaignFactory(
        status=Campaign.Status.DRAFT,
        start_date=timezone.localdate() - timezone.timedelta(days=30),
        end_date=timezone.localdate() - timezone.timedelta(days=2),
        target_segment=SegmentFactory(),
    )
    with pytest.raises(ValidationError):
        set_campaign_status(campaign, Campaign.Status.RUNNING)


def test_campaign_performance_reports_money_as_decimal():
    segment = SegmentFactory()
    campaign = CampaignFactory(
        target_segment=segment,
        budget=Decimal("1000.00"),
        actual_spend=Decimal("400.00"),
        revenue_attributed=Decimal("1600.00"),
        sent_count=100,
        opened_count=50,
        converted_count=10,
    )
    report = campaign_performance(campaign)
    assert report["profit"] == Decimal("1200.00")
    assert report["roi"] == Decimal("300.00")
    assert report["is_profitable"] is True
    assert isinstance(report["spend"], Decimal)


def test_over_budget_campaigns_are_flagged():
    campaign = CampaignFactory(budget=Decimal("100.00"), actual_spend=Decimal("150.00"))
    assert campaign_performance(campaign)["is_over_budget"] is True


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def test_retention_stats_return_the_expected_shape():
    start = timezone.now() - timezone.timedelta(days=30)
    stats = customer_retention_stats(start, timezone.now())
    for key in (
        "active_customers",
        "new_customers",
        "returning_customers",
        "repeat_rate",
        "churn_candidates",
        "data_available",
    ):
        assert key in stats
    assert stats["new_customers"] + stats["returning_customers"] == stats["active_customers"]
