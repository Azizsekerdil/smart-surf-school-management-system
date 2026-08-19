"""Factories for notification tests (and for any module that needs a recipient)."""

from __future__ import annotations

import factory
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationLevel,
    NotificationPreference,
    NotificationTemplate,
)

User = get_user_model()

TEST_PASSWORD = "surf-school-test-pw-2024"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"staff{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@surf.test")
    first_name = "Test"
    last_name = factory.Sequence(lambda n: f"User{n}")
    role = Role.RECEPTION
    language = "en"
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or TEST_PASSWORD)
        self.save(update_fields=["password"])


class NotificationPreferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NotificationPreference

    user = factory.SubFactory(UserFactory)
    in_app_enabled = True
    email_enabled = True
    categories_muted = factory.List([])


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    recipient = factory.SubFactory(UserFactory)
    category = NotificationCategory.SYSTEM
    level = NotificationLevel.INFO
    title = factory.Sequence(lambda n: f"Notification {n}")
    body = "Something happened on the beach."
    link_url = ""
    is_read = False


class NotificationTemplateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NotificationTemplate

    code = factory.Sequence(lambda n: f"template-{n}")
    category = NotificationCategory.BOOKING
    level = NotificationLevel.INFO
    title_en = "Booking {{ code }} confirmed"
    title_tr = "{{ code }} rezervasyonu onaylandı"
    body_en = "See you at {{ time }}."
    body_tr = "{{ time }} saatinde görüşmek üzere."
    is_active = True
