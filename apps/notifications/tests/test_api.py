"""REST API: inbox scoping, self-service actions and the capability matrix."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.accounts.constants import Role
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationPreference,
)

from .factories import NotificationFactory, NotificationTemplateFactory, UserFactory

pytestmark = pytest.mark.django_db


def _json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------
def test_list_is_scoped_to_the_caller(client):
    user = UserFactory()
    mine = NotificationFactory(recipient=user)
    NotificationFactory()

    client.force_login(user)
    response = client.get(reverse("notification-list"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == mine.pk


def test_anonymous_callers_are_rejected(client):
    assert client.get(reverse("notification-list")).status_code in (401, 403)


def test_another_users_notification_is_not_retrievable(client):
    notification = NotificationFactory()
    client.force_login(UserFactory())

    assert client.get(reverse("notification-detail", args=[notification.pk])).status_code == 404


def test_read_and_unread_actions(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, is_read=False)
    client.force_login(user)

    response = client.post(reverse("notification-read", args=[notification.pk]))
    assert response.status_code == 200
    assert response.json()["is_read"] is True

    response = client.post(reverse("notification-unread", args=[notification.pk]))
    assert response.json()["is_read"] is False


def test_read_all_and_unread_count(client):
    user = UserFactory()
    NotificationFactory.create_batch(3, recipient=user, is_read=False)
    client.force_login(user)

    assert client.get(reverse("notification-unread-count")).json()["unread"] == 3

    response = client.post(reverse("notification-read-all"))
    assert response.json() == {"marked_read": 3, "unread": 0}


def test_a_customer_may_manage_their_own_inbox(client):
    """``notifications.view`` is enough — a customer has no ``.change``."""
    customer = UserFactory(role=Role.CUSTOMER)
    notification = NotificationFactory(recipient=customer, is_read=False)
    client.force_login(customer)

    assert client.post(reverse("notification-read", args=[notification.pk])).status_code == 200
    assert client.delete(reverse("notification-detail", args=[notification.pk])).status_code == 204
    assert not Notification.objects.filter(pk=notification.pk).exists()


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------
def test_broadcast_requires_the_add_capability(client):
    client.force_login(UserFactory(role=Role.PHOTOGRAPHER))

    response = _json(
        client,
        reverse("notification-broadcast"),
        {"roles": [Role.LIFEGUARD.value], "title": "Nope"},
    )
    assert response.status_code == 403


def test_broadcast_delivers(client):
    sender = UserFactory(role=Role.OPERATIONS_MANAGER)
    lifeguard = UserFactory(role=Role.LIFEGUARD)
    client.force_login(sender)

    response = _json(
        client,
        reverse("notification-broadcast"),
        {
            "roles": [Role.LIFEGUARD.value],
            "category": NotificationCategory.SAFETY.value,
            "level": "warning",
            "title": "Rip current at the north end",
            "body": "Keep beginners south of the tower.",
        },
    )

    assert response.status_code == 201
    assert response.json()["delivered"] == 1
    assert Notification.objects.filter(recipient=lifeguard).count() == 1


def test_broadcast_rejects_an_external_link(client):
    client.force_login(UserFactory(role=Role.OPERATIONS_MANAGER))

    response = _json(
        client,
        reverse("notification-broadcast"),
        {
            "roles": [Role.LIFEGUARD.value],
            "title": "Click",
            "link_url": "https://evil.example.com",
        },
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def test_preference_me_endpoint_is_self_service(client):
    customer = UserFactory(role=Role.CUSTOMER)
    client.force_login(customer)

    url = reverse("notification-preference-me")
    assert client.get(url).status_code == 200

    response = client.patch(
        url,
        data=json.dumps({"email_enabled": False, "categories_muted": ["crm"]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    preference = NotificationPreference.objects.get(user=customer)
    assert preference.email_enabled is False
    assert preference.categories_muted == ["crm"]


def test_preference_list_never_leaks_other_users(client):
    other = UserFactory()
    NotificationPreference.objects.create(user=other)
    client.force_login(UserFactory())

    assert client.get(reverse("notification-preference-list")).json()["count"] == 0


def test_incomplete_quiet_hours_are_rejected(client):
    user = UserFactory()
    client.force_login(user)

    response = client.patch(
        reverse("notification-preference-me"),
        data=json.dumps({"quiet_hours_start": "22:00"}),
        content_type="application/json",
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def test_template_editing_requires_more_than_view(client):
    NotificationTemplateFactory(code="welcome")
    client.force_login(UserFactory(role=Role.STUDENT))

    assert client.get(reverse("notification-template-list")).status_code == 200
    response = _json(
        client, reverse("notification-template-list"), {"code": "x", "title_en": "X"}
    )
    assert response.status_code == 403


def test_template_preview_renders_in_the_sandbox(client):
    template = NotificationTemplateFactory(code="preview-me")
    client.force_login(UserFactory(role=Role.MARKETING))

    response = _json(
        client,
        reverse("notification-template-preview", args=[template.pk]),
        {"language": "en", "context": {"code": "BK-3", "time": "08:00"}},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Booking BK-3 confirmed"
