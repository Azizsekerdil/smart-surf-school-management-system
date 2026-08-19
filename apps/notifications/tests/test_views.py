"""HTML views: scoping, capability gates, HTMX behaviour and the badge."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.constants import Role
from apps.notifications.context_processors import unread_notifications
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationPreference,
)

from .factories import NotificationFactory, UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
def test_list_shows_only_your_own_notifications(client):
    user = UserFactory()
    mine = NotificationFactory(recipient=user, title="Mine only")
    NotificationFactory(title="Somebody else's")

    client.force_login(user)
    response = client.get(reverse("notifications:list"))

    assert response.status_code == 200
    assert list(response.context["notifications"]) == [mine]
    assert b"Somebody else" not in response.content


def test_list_filters_by_unread_and_category(client):
    user = UserFactory()
    unread_booking = NotificationFactory(
        recipient=user, category=NotificationCategory.BOOKING, is_read=False
    )
    NotificationFactory(recipient=user, category=NotificationCategory.BOOKING, is_read=True)
    NotificationFactory(recipient=user, category=NotificationCategory.PAYMENT, is_read=False)

    client.force_login(user)
    response = client.get(
        reverse("notifications:list"),
        {"unread": "1", "category": NotificationCategory.BOOKING},
    )

    assert list(response.context["notifications"]) == [unread_booking]


def test_list_renders_the_partial_for_htmx(client):
    user = UserFactory()
    NotificationFactory(recipient=user)
    client.force_login(user)

    response = client.get(reverse("notifications:list"), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "notifications/partials/notification_list.html" in [
        t.name for t in response.templates
    ]


def test_list_requires_the_capability(client):
    user = UserFactory(denied_capabilities=["notifications.view"])
    client.force_login(user)

    assert client.get(reverse("notifications:list")).status_code == 403


def test_list_requires_authentication(client):
    response = client.get(reverse("notifications:list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


# ---------------------------------------------------------------------------
# Read state
# ---------------------------------------------------------------------------
def test_mark_read_swaps_the_row_and_refreshes_the_counter(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, is_read=False)
    client.force_login(user)

    response = client.post(
        reverse("notifications:mark_read", args=[notification.pk]), HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert b"hx-swap-oob" in response.content
    notification.refresh_from_db()
    assert notification.is_read is True


def test_mark_read_rejects_another_users_notification(client):
    notification = NotificationFactory(is_read=False)
    client.force_login(UserFactory())

    response = client.post(reverse("notifications:mark_read", args=[notification.pk]))

    assert response.status_code == 404
    notification.refresh_from_db()
    assert notification.is_read is False


def test_mark_read_refuses_get(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)
    client.force_login(user)

    response = client.get(reverse("notifications:mark_read", args=[notification.pk]))
    assert response.status_code == 405


def test_mark_all_read_clears_the_inbox(client):
    user = UserFactory()
    NotificationFactory.create_batch(3, recipient=user, is_read=False)
    client.force_login(user)

    response = client.post(reverse("notifications:mark_all_read"))

    assert response.status_code == 302
    assert Notification.objects.filter(recipient=user, is_read=False).count() == 0


def test_mark_all_read_ignores_an_off_site_next(client):
    user = UserFactory()
    client.force_login(user)

    response = client.post(
        reverse("notifications:mark_all_read"), {"next": "https://evil.example.com/"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notifications:list")


def test_open_marks_read_and_follows_a_relative_link(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, link_url="/bookings/7/")
    client.force_login(user)

    response = client.post(reverse("notifications:open", args=[notification.pk]))

    assert response.status_code == 302
    assert response.url == "/bookings/7/"
    notification.refresh_from_db()
    assert notification.is_read is True


def test_open_refuses_to_redirect_off_site(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, link_url="https://evil.example.com/")
    client.force_login(user)

    response = client.post(reverse("notifications:open", args=[notification.pk]))

    assert response.url == reverse("notifications:list")


# ---------------------------------------------------------------------------
# Dropdown
# ---------------------------------------------------------------------------
def test_dropdown_returns_the_ten_most_recent(client):
    user = UserFactory()
    NotificationFactory.create_batch(14, recipient=user)
    client.force_login(user)

    response = client.get(reverse("notifications:dropdown"))

    assert response.status_code == 200
    assert len(response.context["notifications"]) == 10


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def test_preferences_are_created_on_first_visit(client):
    user = UserFactory()
    client.force_login(user)

    assert client.get(reverse("notifications:preferences")).status_code == 200
    assert NotificationPreference.objects.filter(user=user).exists()


def test_preferences_can_be_saved(client):
    user = UserFactory()
    client.force_login(user)

    response = client.post(
        reverse("notifications:preferences"),
        {
            "in_app_enabled": "on",
            "categories_muted": [NotificationCategory.CRM],
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        },
    )

    assert response.status_code == 302
    preference = NotificationPreference.objects.get(user=user)
    assert preference.email_enabled is False
    assert preference.categories_muted == [NotificationCategory.CRM.value]
    assert preference.quiet_hours_start.hour == 22


def test_half_a_quiet_window_is_rejected(client):
    user = UserFactory()
    client.force_login(user)

    response = client.post(
        reverse("notifications:preferences"),
        {"in_app_enabled": "on", "quiet_hours_start": "22:00"},
    )

    assert response.status_code == 200
    assert response.context["form"].errors


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------
def test_broadcast_needs_the_add_capability(client):
    client.force_login(UserFactory(role=Role.PHOTOGRAPHER))
    assert client.get(reverse("notifications:broadcast")).status_code == 403


def test_broadcast_delivers_to_the_selected_roles(client):
    sender = UserFactory(role=Role.MARKETING)
    instructor = UserFactory(role=Role.SURF_INSTRUCTOR)
    client.force_login(sender)

    response = client.post(
        reverse("notifications:broadcast"),
        {
            "roles": [Role.SURF_INSTRUCTOR],
            "category": NotificationCategory.SYSTEM,
            "level": "info",
            "title": "Staff meeting at 17:00",
            "body": "Beach hut, ten minutes.",
            "link_url": "",
        },
    )

    assert response.status_code == 302
    assert Notification.objects.filter(recipient=instructor).count() == 1
    assert Notification.objects.filter(recipient=sender).count() == 0


def test_broadcast_rejects_an_external_link(client):
    client.force_login(UserFactory(role=Role.MARKETING))

    response = client.post(
        reverse("notifications:broadcast"),
        {
            "roles": [Role.SURF_INSTRUCTOR],
            "category": NotificationCategory.SYSTEM,
            "level": "info",
            "title": "Click me",
            "link_url": "https://evil.example.com",
        },
    )

    assert response.status_code == 200
    assert "link_url" in response.context["form"].errors


# ---------------------------------------------------------------------------
# Context processor
# ---------------------------------------------------------------------------
def test_badge_is_zero_for_anonymous_visitors():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    assert unread_notifications(request) == {"unread_notification_count": 0}


def test_badge_counts_unread_and_is_cached_per_request(django_assert_num_queries):
    user = UserFactory()
    NotificationFactory.create_batch(3, recipient=user, is_read=False)
    NotificationFactory(recipient=user, is_read=True)

    request = RequestFactory().get("/")
    request.user = user

    with django_assert_num_queries(1):
        first = unread_notifications(request)
        second = unread_notifications(request)

    assert first == second == {"unread_notification_count": 3}
