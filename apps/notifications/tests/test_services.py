"""Service rules: delivery policy, read state, reminders and link safety."""

from __future__ import annotations

import datetime as dt

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.constants import Role
from apps.notifications import services
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationLevel,
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
# notify()
# ---------------------------------------------------------------------------
def test_notify_stores_the_message():
    user = UserFactory()

    notification = services.notify(
        user,
        NotificationCategory.BOOKING,
        "Booking BK-1 confirmed",
        "Two seats, Saturday 09:00.",
        link_url="/bookings/1/",
    )

    assert notification is not None
    assert notification.recipient == user
    assert notification.is_read is False
    assert notification.link_url == "/bookings/1/"
    assert Notification.objects.filter(recipient=user).count() == 1


def test_notify_records_a_soft_reference():
    user = UserFactory()
    notification = services.notify(
        user, NotificationCategory.SYSTEM, "Account touched", related=user
    )
    assert notification.related_object_type == "accounts.user"
    assert notification.related_object_id == user.pk


def test_notify_is_silent_for_a_muted_category():
    user = UserFactory()
    NotificationPreferenceFactory(user=user, categories_muted=[NotificationCategory.CRM])

    assert services.notify(user, NotificationCategory.CRM, "New lead") is None
    assert Notification.objects.filter(recipient=user).count() == 0


def test_notify_skips_when_in_app_is_disabled():
    user = UserFactory()
    NotificationPreferenceFactory(user=user, in_app_enabled=False, email_enabled=False)

    assert services.notify(user, NotificationCategory.SYSTEM, "Ignored") is None


def test_notify_skips_inactive_users():
    user = UserFactory(is_active=False)
    assert services.notify(user, NotificationCategory.SYSTEM, "Nope") is None


def test_notify_never_raises_on_bad_input():
    assert services.notify(None, NotificationCategory.SYSTEM, "Nobody") is None
    assert services.notify(UserFactory(), NotificationCategory.SYSTEM, "") is None


def test_notify_normalises_unknown_vocabulary():
    notification = services.notify(
        UserFactory(), "not-a-category", "Hello", level="not-a-level"
    )
    assert notification.category == NotificationCategory.SYSTEM
    assert notification.level == NotificationLevel.INFO


def test_notify_rejects_off_site_links():
    notification = services.notify(
        UserFactory(),
        NotificationCategory.SYSTEM,
        "Phishy",
        link_url="https://evil.example.com/steal",
    )
    assert notification.link_url == ""


def test_notify_truncates_an_over_long_title():
    notification = services.notify(
        UserFactory(), NotificationCategory.SYSTEM, "x" * 500
    )
    assert len(notification.title) == 200


def test_warnings_are_emailed_and_information_is_not(django_capture_on_commit_callbacks):
    user = UserFactory(email="coach@surf.test")
    NotificationPreferenceFactory(user=user, email_enabled=True)

    # The e-mail is queued on commit so a rolled-back transaction cannot send it.
    with django_capture_on_commit_callbacks(execute=True):
        notification = services.notify(
            user, NotificationCategory.SAFETY, "Beach closed", level="warning"
        )
    assert len(mail.outbox) == 1
    assert "Beach closed" in mail.outbox[0].subject
    notification.refresh_from_db()
    assert notification.is_emailed is True

    with django_capture_on_commit_callbacks(execute=True):
        services.notify(user, NotificationCategory.BOOKING, "Routine update")
    assert len(mail.outbox) == 1


def test_quiet_hours_suppress_the_email_but_keep_the_entry():
    user = UserFactory(email="coach@surf.test")
    now = timezone.localtime()
    NotificationPreferenceFactory(
        user=user,
        email_enabled=True,
        quiet_hours_start=(now - dt.timedelta(hours=1)).time(),
        quiet_hours_end=(now + dt.timedelta(hours=1)).time(),
    )

    notification = services.notify(
        user, NotificationCategory.SAFETY, "Storm warning", level="warning"
    )

    assert notification is not None
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# notify_role() / notify_from_template()
# ---------------------------------------------------------------------------
def test_notify_role_reaches_active_holders_only():
    lifeguard = UserFactory(role=Role.LIFEGUARD)
    retired = UserFactory(role=Role.LIFEGUARD, is_active=False)
    reception = UserFactory(role=Role.RECEPTION)

    delivered = services.notify_role(
        Role.LIFEGUARD, NotificationCategory.WEATHER, "Red flag raised"
    )

    recipients = {n.recipient_id for n in delivered}
    assert recipients == {lifeguard.pk}
    assert retired.pk not in recipients
    assert reception.pk not in recipients


def test_notify_role_accepts_several_roles_and_excludes_the_sender():
    sender = UserFactory(role=Role.LIFEGUARD)
    other_lifeguard = UserFactory(role=Role.LIFEGUARD)
    instructor = UserFactory(role=Role.SURF_INSTRUCTOR)

    delivered = services.notify_role(
        [Role.LIFEGUARD, Role.SURF_INSTRUCTOR],
        NotificationCategory.SAFETY,
        "Everyone out of the water",
        exclude_user=sender,
    )

    assert {n.recipient_id for n in delivered} == {other_lifeguard.pk, instructor.pk}


def test_notify_from_template_uses_the_recipient_language():
    user = UserFactory(language="tr")
    NotificationTemplateFactory(code="booking-confirmed")

    notification = services.notify_from_template(
        "booking-confirmed", user, {"code": "BK-9", "time": "10:00"}
    )

    assert notification is not None
    assert "BK-9" in notification.title
    assert notification.category == NotificationCategory.BOOKING


def test_notify_from_template_ignores_an_unknown_or_inactive_code():
    user = UserFactory()
    NotificationTemplateFactory(code="retired-template", is_active=False)

    assert services.notify_from_template("does-not-exist", user, {}) is None
    assert services.notify_from_template("retired-template", user, {}) is None


# ---------------------------------------------------------------------------
# Read state
# ---------------------------------------------------------------------------
def test_mark_read_requires_ownership():
    owner = UserFactory()
    intruder = UserFactory()
    notification = NotificationFactory(recipient=owner)

    with pytest.raises(PermissionDenied):
        services.mark_read(notification, intruder)

    notification.refresh_from_db()
    assert notification.is_read is False
    assert services.mark_read(notification, owner) is True


def test_mark_all_read_can_be_limited_to_one_category():
    user = UserFactory()
    NotificationFactory.create_batch(2, recipient=user, category=NotificationCategory.BOOKING)
    NotificationFactory(recipient=user, category=NotificationCategory.PAYMENT)
    NotificationFactory()  # somebody else's inbox

    updated = services.mark_all_read(user, category=NotificationCategory.BOOKING)

    assert updated == 2
    assert Notification.objects.filter(recipient=user, is_read=False).count() == 1


def test_mark_all_read_ignores_anonymous_callers():
    assert services.mark_all_read(None) == 0


# ---------------------------------------------------------------------------
# Reminders and housekeeping
# ---------------------------------------------------------------------------
def test_send_lesson_reminders_is_a_no_op_without_the_bookings_module():
    """The reminder degrades to zero rather than exploding when apps are absent."""
    assert services.send_lesson_reminders() == 0


class _FakeField:
    def __init__(self, name, internal_type):
        self.name = name
        self._internal_type = internal_type

    def get_internal_type(self):
        return self._internal_type


class _FakeMeta:
    def __init__(self, fields):
        self._fields = fields

    def get_fields(self):
        return self._fields


def _fake_model(*fields):
    return type("FakeLesson", (), {"_meta": _FakeMeta([_FakeField(*f) for f in fields])})


def test_lesson_start_field_probe_prefers_the_canonical_name():
    model = _fake_model(
        ("id", "BigAutoField"),
        ("created_at", "DateTimeField"),
        ("start_datetime", "DateTimeField"),
        ("start_at", "DateTimeField"),
    )
    assert services._lesson_start_field(model) == "start_datetime"


def test_lesson_start_field_probe_falls_back_to_any_start_datetime():
    model = _fake_model(("id", "BigAutoField"), ("session_start_utc", "DateTimeField"))
    assert services._lesson_start_field(model) == "session_start_utc"


def test_lesson_start_field_probe_ignores_a_plain_date():
    model = _fake_model(("id", "BigAutoField"), ("start_date", "DateField"))
    assert services._lesson_start_field(model) is None


class _Holder:
    """Stands in for a Customer/Student row that may link to a login."""

    def __init__(self, user):
        self.user = user


class _FakeLesson:
    pk = 1
    instructor = None
    spot = "North Point"

    def __init__(self, starts_at):
        self.start_datetime = starts_at

    def __str__(self):
        return "Beginner group lesson"


class _FakeBooking:
    pk = 1

    def __init__(self, lesson, customer=None, student=None):
        self.lesson = lesson
        self.customer = customer
        self.student = student


def test_reminder_reaches_the_customer_and_the_student_once_each():
    customer_user = UserFactory()
    student_user = UserFactory()
    now = timezone.now()
    lesson = _FakeLesson(now + dt.timedelta(minutes=35))
    booking = _FakeBooking(lesson, _Holder(customer_user), _Holder(student_user))

    sent = services._remind_one_booking(booking, "start_datetime", now, set())

    assert sent == 2
    reminder = Notification.objects.get(recipient=customer_user)
    assert reminder.category == NotificationCategory.LESSON_REMINDER
    assert "35" in reminder.title
    assert "North Point" in reminder.body


def test_a_customer_booking_for_themselves_is_reminded_once():
    user = UserFactory()
    now = timezone.now()
    booking = _FakeBooking(
        _FakeLesson(now + dt.timedelta(minutes=32)), _Holder(user), _Holder(user)
    )

    assert services._remind_one_booking(booking, "start_datetime", now, set()) == 1
    assert Notification.objects.filter(recipient=user).count() == 1


def test_a_booking_with_no_login_accounts_sends_nothing():
    now = timezone.now()
    booking = _FakeBooking(_FakeLesson(now + dt.timedelta(minutes=31)))

    assert services._remind_one_booking(booking, "start_datetime", now, set()) == 0
    assert Notification.objects.count() == 0


def test_purge_only_removes_read_notifications():
    user = UserFactory()
    old_read = NotificationFactory(recipient=user, is_read=True)
    old_unread = NotificationFactory(recipient=user, is_read=False)
    long_ago = timezone.now() - dt.timedelta(days=400)
    Notification.objects.filter(pk__in=[old_read.pk, old_unread.pk]).update(created_at=long_ago)

    purged = services.purge_old_notifications(days=180)

    assert purged == 1
    assert Notification.objects.filter(pk=old_unread.pk).exists()
    assert not Notification.objects.filter(pk=old_read.pk).exists()


def test_preferences_are_loaded_once_for_a_broadcast(django_assert_max_num_queries):
    for _ in range(5):
        NotificationPreference.objects.create(user=UserFactory(role=Role.LIFEGUARD))

    # 1 recipient query + 1 preference query + 5 inserts — never one per user.
    with django_assert_max_num_queries(7):
        services.notify_role(Role.LIFEGUARD, NotificationCategory.WEATHER, "Wind picking up")
