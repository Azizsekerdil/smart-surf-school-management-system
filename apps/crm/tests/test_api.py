"""REST API contract."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.constants import Role
from apps.crm.models import Campaign, Lead

from .factories import CampaignFactory, InteractionFactory, LeadFactory, SegmentFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client(client):
    client.force_login(UserFactory(role=Role.MARKETING))
    return client


def test_routes_are_declared():
    from apps.crm.api import ROUTES

    prefixes = {prefix for prefix, _viewset, _basename in ROUTES}
    assert prefixes == {
        "crm/leads",
        "crm/interactions",
        "crm/segments",
        "crm/campaigns",
        "crm/retention",
    }


def test_anonymous_access_is_refused(client):
    response = client.get(reverse("crm-lead-list"))
    assert response.status_code in (401, 403)


def test_a_role_without_crm_capability_is_refused(client):
    client.force_login(UserFactory(role=Role.PHOTOGRAPHER))
    response = client.get(reverse("crm-lead-list"))
    assert response.status_code == 403


def test_lead_list_and_detail(api_client):
    lead = LeadFactory()
    response = api_client.get(reverse("crm-lead-list"))
    assert response.status_code == 200

    detail = api_client.get(reverse("crm-lead-detail", kwargs={"pk": lead.pk}))
    assert detail.status_code == 200
    assert detail.json()["full_name"] == lead.full_name
    assert "weighted_value" in detail.json()


def test_creating_a_lead_without_contact_details_is_rejected(api_client):
    response = api_client.post(
        reverse("crm-lead-list"),
        {"first_name": "Ghost", "expected_value": "0.00", "probability": "10.00"},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_a_lead_cannot_be_won_through_a_plain_update(api_client):
    lead = LeadFactory()
    response = api_client.patch(
        reverse("crm-lead-detail", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.WON},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_advance_action_moves_the_lead(api_client):
    lead = LeadFactory(status=Lead.Status.NEW)
    response = api_client.post(
        reverse("crm-lead-advance", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.CONTACTED},
        content_type="application/json",
    )
    assert response.status_code == 200
    lead.refresh_from_db()
    assert lead.status == Lead.Status.CONTACTED


def test_convert_action_creates_the_customer(api_client):
    lead = LeadFactory()
    response = api_client.post(
        reverse("crm-lead-convert", kwargs={"pk": lead.pk}), {}, content_type="application/json"
    )
    assert response.status_code == 200
    assert response.json()["customer_id"]
    lead.refresh_from_db()
    assert lead.status == Lead.Status.WON


def test_funnel_action_returns_every_stage(api_client):
    LeadFactory()
    response = api_client.get(reverse("crm-lead-funnel"))
    assert response.status_code == 200
    assert len(response.json()) == len(Lead.Status.choices)


def test_interaction_create_requires_a_target(api_client):
    response = api_client.post(
        reverse("crm-interaction-list"),
        {"kind": "call", "direction": "outbound", "subject": "Nobody"},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_interaction_follow_up_can_be_completed(api_client):
    from django.utils import timezone

    interaction = InteractionFactory(
        follow_up_required=True, follow_up_at=timezone.now() + timezone.timedelta(days=1)
    )
    response = api_client.post(
        reverse("crm-interaction-complete-follow-up", kwargs={"pk": interaction.pk}),
        {},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["follow_up_required"] is False


def test_segment_criteria_are_validated_by_the_api(api_client):
    response = api_client.post(
        reverse("crm-segment-list"),
        {"name": "Injected", "criteria": {"email__contains": "@"}},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_valid_segment_criteria_are_accepted_and_counted(api_client):
    response = api_client.post(
        reverse("crm-segment-list"),
        {"name": "Consenting beginners", "criteria": {"surf_level": ["beginner"]}},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert response.json()["rules"]


def test_segment_refresh_and_members(api_client):
    segment = SegmentFactory()
    refresh = api_client.post(
        reverse("crm-segment-refresh", kwargs={"pk": segment.pk}), {}, content_type="application/json"
    )
    assert refresh.status_code == 200

    members = api_client.get(reverse("crm-segment-members", kwargs={"pk": segment.pk}))
    assert members.status_code == 200
    assert "results" in members.json()


def test_campaign_performance_action(api_client):
    campaign = CampaignFactory()
    response = api_client.get(reverse("crm-campaign-performance", kwargs={"pk": campaign.pk}))
    assert response.status_code == 200
    assert "roi" in response.json()


def test_campaign_status_action_refuses_an_illegal_move(api_client):
    campaign = CampaignFactory(status=Campaign.Status.DRAFT)
    response = api_client.post(
        reverse("crm-campaign-set-status", kwargs={"pk": campaign.pk}),
        {"status": Campaign.Status.COMPLETED},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_campaign_counters_must_be_consistent(api_client):
    response = api_client.post(
        reverse("crm-campaign-list"),
        {
            "name": "Impossible",
            "channel": "email",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "sent_count": 10,
            "opened_count": 20,
        },
        content_type="application/json",
    )
    assert response.status_code == 400


def test_retention_endpoint_returns_the_tiles(api_client):
    response = api_client.get(reverse("crm-retention-list"))
    assert response.status_code == 200
    assert "repeat_rate" in response.json()
