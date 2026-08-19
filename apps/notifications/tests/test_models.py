"""Model behaviour: read state, the template sandbox and preference rules."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.notifications.models import (
    NotificationCategory,
    NotificationChannel,
    NotificationPreference,
)

from .factories import (
    NotificationFactory,
    NotificationPreferenceFactory,
    NotificationTemplateFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
def test_str_is_the_title():
    notification = NotificationFactory(title="Lesson starts in 30 minutes")
    assert str(notification) == "Lesson starts in 30 minutes"


def test_mark_read_is_idempotent():
    notification = NotificationFactory(is_read=False)

    assert notification.mark_read() is True
    assert notification.is_read is True
    assert notification.read_at is not None

    first_timestamp = notification.read_at
    assert notification.mark_read() is False
    assert notification.read_at == first_timestamp


def test_mark_unread_clears_the_timestamp():
    notification = NotificationFactory(is_read=True, read_at=timezone.now())
    assert notification.mark_unread() is True
    assert notification.read_at is None


def test_set_related_stores_a_soft_reference():
    user = UserFactory()
    notification = NotificationFactory(recipient=user)
    notification.set_related(user)

    assert notification.related_object_type == "accounts.user"
    assert notification.related_object_id == user.pk
    assert notification.related_label == f"accounts.user #{user.pk}"


def test_set_related_ignores_unsaved_objects():
    notification = NotificationFactory()
    notification.set_related(None)
    assert notification.related_object_type == ""
    assert notification.related_object_id is None


def test_category_drives_the_icon_and_level_drives_the_badge():
    notification = NotificationFactory(
        category=NotificationCategory.SAFETY, level="error"
    )
    assert notification.icon_name == "shield-alert"
    assert notification.badge_color == "rose"


def test_soft_delete_hides_the_row_from_the_default_manager():
    from apps.notifications.models import Notification

    notification = NotificationFactory()
    notification.delete()

    assert not Notification.objects.filter(pk=notification.pk).exists()
    assert Notification.all_objects.filter(pk=notification.pk).exists()


# ---------------------------------------------------------------------------
# NotificationTemplate
# ---------------------------------------------------------------------------
def test_template_renders_the_requested_language():
    template = NotificationTemplateFactory()

    title_en, body_en = template.render("en", {"code": "BK-1042", "time": "09:30"})
    title_tr, _body_tr = template.render("tr", {"code": "BK-1042", "time": "09:30"})

    assert title_en == "Booking BK-1042 confirmed"
    assert body_en == "See you at 09:30."
    assert title_tr.startswith("BK-1042")


def test_template_falls_back_to_english_when_turkish_is_missing():
    template = NotificationTemplateFactory(title_tr="", body_tr="")
    title, body = template.render("tr", {"code": "BK-7", "time": "11:00"})
    assert title == "Booking BK-7 confirmed"
    assert body == "See you at 11:00."


def test_template_escapes_context_values():
    template = NotificationTemplateFactory(
        title_en="Hello {{ name }}", title_tr="", body_en="", body_tr=""
    )
    title, _body = template.render("en", {"name": "<script>alert(1)</script>"})
    assert "<script>" not in title
    assert "&lt;script&gt;" in title


def test_template_cannot_traverse_a_model_instance():
    """A non-scalar context value is stringified, so no attribute is reachable."""
    user = UserFactory(username="coach")
    template = NotificationTemplateFactory(
        title_en="User {{ who.password }}|{{ who }}", title_tr="", body_en="", body_tr=""
    )
    title, _body = template.render("en", {"who": user})

    assert "User |" in title  # the dotted lookup resolved to nothing
    assert str(user) in title


def test_template_cannot_load_a_tag_library():
    template = NotificationTemplateFactory(
        title_en="{% load surf_tags %}{% icon 'bell' %}",
        title_tr="",
        body_en="",
        body_tr="",
    )
    title, _body = template.render("en", {})
    # A syntax error falls back to the raw source instead of executing anything.
    assert "<svg" not in title
    assert "load surf_tags" in title


def test_template_title_is_truncated_and_single_line():
    template = NotificationTemplateFactory(
        title_en="{{ padding }}", title_tr="", body_en="", body_tr=""
    )
    title, _body = template.render("en", {"padding": "a\nb " + "x" * 500})
    assert "\n" not in title
    assert len(title) <= 200


# ---------------------------------------------------------------------------
# NotificationPreference
# ---------------------------------------------------------------------------
def test_muted_category_is_blocked_on_every_channel():
    preference = NotificationPreferenceFactory(
        categories_muted=[NotificationCategory.CRM]
    )
    assert preference.allows(NotificationCategory.CRM, NotificationChannel.IN_APP) is False
    assert preference.allows(NotificationCategory.CRM, NotificationChannel.EMAIL) is False
    assert preference.allows(NotificationCategory.SAFETY, NotificationChannel.IN_APP) is True


def test_quiet_hours_only_suppress_email():
    # A window built around "now" is quiet whatever time the suite runs at.
    now = timezone.localtime()
    preference = NotificationPreferenceFactory(
        quiet_hours_start=(now - dt.timedelta(hours=1)).time(),
        quiet_hours_end=(now + dt.timedelta(hours=1)).time(),
    )
    assert preference.is_quiet_at() is True
    assert preference.allows(NotificationCategory.BOOKING, NotificationChannel.EMAIL) is False
    assert preference.allows(NotificationCategory.BOOKING, NotificationChannel.IN_APP) is True


def test_quiet_hours_wrap_past_midnight():
    preference = NotificationPreferenceFactory(
        quiet_hours_start=dt.time(22, 0), quiet_hours_end=dt.time(7, 0)
    )
    midnight = timezone.localtime().replace(hour=2, minute=30)
    afternoon = timezone.localtime().replace(hour=14, minute=0)

    assert preference.is_quiet_at(midnight) is True
    assert preference.is_quiet_at(afternoon) is False


def test_incomplete_quiet_hours_are_ignored():
    preference = NotificationPreferenceFactory(
        quiet_hours_start=dt.time(22, 0), quiet_hours_end=None
    )
    assert preference.has_quiet_hours is False
    assert preference.is_quiet_at() is False


def test_for_user_does_not_write_by_default():
    user = UserFactory()

    preference = NotificationPreference.for_user(user)
    assert preference.pk is None
    assert NotificationPreference.objects.filter(user=user).count() == 0

    created = NotificationPreference.for_user(user, create=True)
    assert created.pk is not None
    assert NotificationPreference.for_user(user).pk == created.pk
