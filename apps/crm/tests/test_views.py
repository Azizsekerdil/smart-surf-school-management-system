"""HTML screens: they render, and they refuse the wrong role."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.constants import Role
from apps.crm.models import Interaction, Lead

from .factories import CampaignFactory, InteractionFactory, LeadFactory, SegmentFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def marketer(client):
    user = UserFactory(role=Role.MARKETING)
    client.force_login(user)
    return user


@pytest.fixture
def photographer(client):
    """A staff role with no CRM capability at all."""
    user = UserFactory(role=Role.PHOTOGRAPHER)
    client.force_login(user)
    return user


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("crm:dashboard"))
    assert response.status_code == 302


def test_a_role_without_crm_view_is_refused(client, photographer):
    response = client.get(reverse("crm:lead_list"))
    assert response.status_code == 403


def test_reading_the_pipeline_does_not_grant_writing_it(client):
    from apps.crm.services import advance_lead_status  # noqa: F401  (documented intent)

    user = UserFactory(role=Role.OPERATIONS_MANAGER)  # crm.view only
    client.force_login(user)
    lead = LeadFactory()
    assert client.get(reverse("crm:lead_list")).status_code == 200
    response = client.post(
        reverse("crm:lead_status", kwargs={"pk": lead.pk}), {"status": Lead.Status.CONTACTED}
    )
    assert response.status_code == 403


def test_converting_requires_the_customers_permission(client):
    """Marketing qualifies leads; it does not create customer records."""
    user = UserFactory(role=Role.MARKETING)
    client.force_login(user)
    assert user.has_capability("crm.change") is True
    assert user.has_capability("customers.add") is False
    lead = LeadFactory()
    response = client.get(reverse("crm:lead_convert", kwargs={"pk": lead.pk}))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_dashboard_renders(client, marketer):
    LeadFactory(status=Lead.Status.QUALIFIED, expected_value=Decimal("500.00"))
    InteractionFactory()
    response = client.get(reverse("crm:dashboard"))
    assert response.status_code == 200
    assert b"CRM" in response.content


def test_lead_list_renders_and_searches(client, marketer):
    LeadFactory(first_name="Kaan", last_name="Deniz")
    LeadFactory(first_name="Elif", last_name="Su")
    response = client.get(reverse("crm:lead_list"), {"q": "Kaan"})
    assert response.status_code == 200
    assert b"Kaan" in response.content
    assert b"Elif" not in response.content


def test_lead_list_filters_by_status(client, marketer):
    LeadFactory(status=Lead.Status.NEW, first_name="Yeni")
    LeadFactory(status=Lead.Status.QUALIFIED, first_name="Nitelikli")
    response = client.get(reverse("crm:lead_list"), {"status": Lead.Status.QUALIFIED})
    assert b"Nitelikli" in response.content
    assert b"Yeni" not in response.content


def test_lead_board_renders_every_stage(client, marketer):
    LeadFactory()
    response = client.get(reverse("crm:lead_board"))
    assert response.status_code == 200
    for value in Lead.Status.values:
        assert f'data-lead-column="{value}"'.encode() in response.content


def test_lead_detail_renders(client, marketer):
    lead = LeadFactory()
    InteractionFactory(lead=lead)
    response = client.get(reverse("crm:lead_detail", kwargs={"pk": lead.pk}))
    assert response.status_code == 200


def test_lead_create_persists_and_redirects(client, marketer):
    response = client.post(
        reverse("crm:lead_create"),
        {
            "first_name": "Mert",
            "last_name": "Aydin",
            "email": "mert@example.test",
            "phone": "",
            "source": "website",
            "interest": "Private lesson",
            "status": Lead.Status.NEW,
            "assigned_to": "",
            "expected_value": "800.00",
            "probability": "30.00",
            "next_action": "",
            "next_action_at": "",
            "lost_reason": "",
        },
    )
    assert response.status_code == 302
    assert Lead.objects.filter(email="mert@example.test").exists()


def test_lead_create_rejects_a_lead_with_no_way_to_reach_them(client, marketer):
    response = client.post(
        reverse("crm:lead_create"),
        {
            "first_name": "Nobody",
            "last_name": "",
            "email": "",
            "phone": "",
            "source": "website",
            "status": Lead.Status.NEW,
            "expected_value": "0.00",
            "probability": "10.00",
        },
    )
    assert response.status_code == 200
    assert not Lead.objects.filter(first_name="Nobody").exists()


# ---------------------------------------------------------------------------
# Kanban moves
# ---------------------------------------------------------------------------
def test_card_move_updates_the_stage_and_returns_the_board(client, marketer):
    lead = LeadFactory(status=Lead.Status.NEW)
    response = client.post(
        reverse("crm:lead_status", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.QUALIFIED},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b'id="lead-board"' in response.content
    lead.refresh_from_db()
    assert lead.status == Lead.Status.QUALIFIED


def test_dropping_a_card_on_won_redirects_to_the_conversion_screen(client, marketer):
    lead = LeadFactory(status=Lead.Status.PROPOSAL_SENT)
    response = client.post(
        reverse("crm:lead_status", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.WON},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204
    assert response["HX-Redirect"].endswith(
        reverse("crm:lead_convert", kwargs={"pk": lead.pk})
    )


def test_losing_a_card_without_a_reason_is_refused(client, marketer):
    lead = LeadFactory(status=Lead.Status.NEW)
    response = client.post(
        reverse("crm:lead_status", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.LOST},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 422
    lead.refresh_from_db()
    assert lead.status == Lead.Status.NEW


def test_lost_reason_can_arrive_through_the_htmx_prompt_header(client, marketer):
    lead = LeadFactory(status=Lead.Status.NEW)
    response = client.post(
        reverse("crm:lead_status", kwargs={"pk": lead.pk}),
        {"status": Lead.Status.LOST},
        HTTP_HX_REQUEST="true",
        HTTP_HX_PROMPT="Chose another school",
    )
    assert response.status_code == 200
    lead.refresh_from_db()
    assert lead.status == Lead.Status.LOST
    assert lead.lost_reason == "Chose another school"


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
def test_interaction_modal_renders_for_a_lead(client, marketer):
    lead = LeadFactory()
    response = client.get(
        reverse("crm:interaction_create"), {"lead": lead.pk}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert b'id="interaction-modal-title"' in response.content


def test_logging_an_interaction_over_htmx_closes_the_modal(client, marketer):
    lead = LeadFactory()
    response = client.post(
        reverse("crm:interaction_create"),
        {
            "lead": lead.pk,
            "customer": "",
            "kind": Interaction.Kind.CALL,
            "direction": Interaction.Direction.OUTBOUND,
            "subject": "Talked about the tide window",
            "body": "",
            "occurred_at": "2026-08-10T09:30",
            "duration_minutes": "",
            "follow_up_required": "",
            "follow_up_at": "",
            "sentiment": "",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response.content == b""
    assert "crm:interaction-logged" in response["HX-Trigger"]
    assert Interaction.objects.filter(lead=lead).count() == 1


def test_interaction_list_renders(client, marketer):
    InteractionFactory()
    response = client.get(reverse("crm:interaction_list"))
    assert response.status_code == 200


def test_follow_up_can_be_closed(client, marketer):
    from django.utils import timezone

    interaction = InteractionFactory(
        follow_up_required=True, follow_up_at=timezone.now() + timezone.timedelta(days=1)
    )
    response = client.post(
        reverse("crm:follow_up_complete", kwargs={"pk": interaction.pk}),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204
    interaction.refresh_from_db()
    assert interaction.follow_up_required is False


# ---------------------------------------------------------------------------
# Campaigns & segments
# ---------------------------------------------------------------------------
def test_campaign_list_and_detail_render(client, marketer):
    campaign = CampaignFactory()
    assert client.get(reverse("crm:campaign_list")).status_code == 200
    assert client.get(
        reverse("crm:campaign_detail", kwargs={"pk": campaign.pk})
    ).status_code == 200


def test_campaign_status_button_moves_the_campaign(client, marketer):
    campaign = CampaignFactory(status="draft", target_segment=SegmentFactory())
    response = client.post(
        reverse("crm:campaign_status", kwargs={"pk": campaign.pk}), {"status": "scheduled"}
    )
    assert response.status_code == 302
    campaign.refresh_from_db()
    assert campaign.status == "scheduled"


def test_segment_screens_render(client, marketer):
    segment = SegmentFactory()
    assert client.get(reverse("crm:segment_list")).status_code == 200
    assert client.get(reverse("crm:segment_detail", kwargs={"pk": segment.pk})).status_code == 200
    assert client.get(reverse("crm:segment_create")).status_code == 200


def test_segment_preview_counts_without_saving(client, marketer):
    response = client.post(
        reverse("crm:segment_preview"),
        {"name": "", "has_email": "true", "min_bookings": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"segment-preview" in response.content


def test_segment_create_stores_only_whitelisted_rules(client, marketer):
    from apps.crm.models import Segment

    response = client.post(
        reverse("crm:segment_create"),
        {
            "name": "Beginners with consent",
            "description": "",
            "is_dynamic": "on",
            "surf_level": ["beginner"],
            "marketing_consent": "true",
            "has_email": "",
            "has_phone": "",
            "city": "",
            "country": "",
            "language": "",
            "tags": "",
            "created_within_days": "",
            "min_lifetime_value": "",
            "min_bookings": "2",
            "max_bookings": "",
            "last_visit_days": "90",
            "no_visit_days": "",
        },
    )
    assert response.status_code == 302
    segment = Segment.objects.get(name="Beginners with consent")
    assert set(segment.criteria) == {
        "surf_level",
        "marketing_consent",
        "min_bookings",
        "last_visit_days",
    }


def test_segment_create_refuses_an_empty_rule_set(client, marketer):
    response = client.post(
        reverse("crm:segment_create"), {"name": "Everyone", "description": "", "is_dynamic": "on"}
    )
    assert response.status_code == 200
