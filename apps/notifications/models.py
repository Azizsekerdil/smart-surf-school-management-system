"""Notification models.

Design notes
------------
*Soft references.* A notification points at the record that caused it with a
``"app_label.modelname"`` string plus an integer id — never a real foreign key.
A booking may be purged, an equipment row retired, a lesson hard-deleted by a
data-retention job; none of that may take the notification history with it, and
none of it may raise while a bell icon is being rendered.

*Preferences are advisory for in-app, binding for e-mail.* Quiet hours never
hide an in-app notification (it is passive — it waits in the list), but they do
stop an e-mail landing on an instructor's phone at 03:00.

*Templates render in a sandbox.* :meth:`NotificationTemplate.render` uses a
private Django template engine with autoescaping on, no template directories,
no ``{% load %}``-able libraries and a context restricted to plain scalar
values, so a stored template can never traverse into a model instance nor pull
an unrelated file into the message body.
"""

from __future__ import annotations

import datetime as dt
import functools
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.template import Context, Engine, TemplateSyntaxError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, SoftDeleteQuerySet, TimeStampedModel


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
class NotificationCategory(models.TextChoices):
    """What part of the operation a notification came from.

    Shared by :class:`Notification` and :class:`NotificationTemplate`, and
    imported by every other module that calls :func:`apps.notifications.services.notify`.
    """

    LESSON_REMINDER = "lesson_reminder", _("Lesson reminder")
    BOOKING = "booking", _("Booking")
    PAYMENT = "payment", _("Payment")
    RENTAL_OVERDUE = "rental_overdue", _("Overdue rental")
    EQUIPMENT = "equipment", _("Equipment")
    MAINTENANCE = "maintenance", _("Maintenance")
    WEATHER = "weather", _("Weather")
    SAFETY = "safety", _("Safety")
    BACKUP = "backup", _("Backup")
    SYSTEM = "system", _("System")
    AI = "ai", _("AI assistant")
    CRM = "crm", _("CRM")


class NotificationLevel(models.TextChoices):
    INFO = "info", _("Information")
    SUCCESS = "success", _("Success")
    WARNING = "warning", _("Warning")
    ERROR = "error", _("Error")


class NotificationChannel(models.TextChoices):
    IN_APP = "in_app", _("In-app")
    EMAIL = "email", _("E-mail")


#: Badge palette per level, consumed by ``{% status_badge %}`` in the templates.
LEVEL_BADGE_COLORS: dict[str, str] = {
    NotificationLevel.INFO: "sky",
    NotificationLevel.SUCCESS: "emerald",
    NotificationLevel.WARNING: "amber",
    NotificationLevel.ERROR: "rose",
}

#: Icon (vendored Lucide name) per category.
CATEGORY_ICONS: dict[str, str] = {
    NotificationCategory.LESSON_REMINDER: "clock",
    NotificationCategory.BOOKING: "calendar-days",
    NotificationCategory.PAYMENT: "wallet",
    NotificationCategory.RENTAL_OVERDUE: "arrow-left-right",
    NotificationCategory.EQUIPMENT: "package",
    NotificationCategory.MAINTENANCE: "wrench",
    NotificationCategory.WEATHER: "cloud-rain",
    NotificationCategory.SAFETY: "shield-alert",
    NotificationCategory.BACKUP: "database-backup",
    NotificationCategory.SYSTEM: "settings",
    NotificationCategory.AI: "sparkles",
    NotificationCategory.CRM: "heart-handshake",
}

#: Levels that justify an e-mail on top of the in-app entry when the caller did
#: not decide explicitly. Routine information stays inside the application.
EMAIL_WORTHY_LEVELS: frozenset[str] = frozenset(
    {NotificationLevel.WARNING, NotificationLevel.ERROR}
)


# ---------------------------------------------------------------------------
# Template sandbox
# ---------------------------------------------------------------------------
#: Value types a stored template may receive. Anything else is stringified, so
#: a caller that accidentally passes a model instance leaks nothing but its
#: ``__str__`` and cannot be traversed with dotted lookups.
_ALLOWED_CONTEXT_TYPES: tuple[type, ...] = (
    str,
    bool,
    int,
    float,
    Decimal,
    dt.date,
    dt.time,
    dt.datetime,
    dt.timedelta,
)

#: Hard ceiling so a malformed template cannot produce a multi-megabyte row.
MAX_RENDERED_TITLE = 200
MAX_RENDERED_BODY = 4000


@functools.lru_cache(maxsize=1)
def _sandbox_engine() -> Engine:
    """A template engine that can only substitute values.

    ``dirs=[]`` + ``app_dirs=False`` make ``{% include %}`` / ``{% extends %}``
    resolve to nothing, and ``libraries={}`` makes ``{% load %}`` a syntax
    error, so the only thing a stored template can do is format the context it
    was handed.
    """
    return Engine(
        dirs=[],
        app_dirs=False,
        libraries={},
        autoescape=True,
        string_if_invalid="",
        debug=False,
    )


def restrict_context(context: dict | None) -> dict:
    """Reduce *context* to plain, non-traversable values."""
    safe: dict[str, object] = {}
    for key, value in (context or {}).items():
        name = str(key)
        if not name.isidentifier() or name.startswith("_"):
            continue
        if value is None:
            safe[name] = ""
        elif isinstance(value, _ALLOWED_CONTEXT_TYPES):
            safe[name] = value
        else:
            safe[name] = str(value)
    return safe


def render_sandboxed(source: str, context: dict, limit: int, *, single_line: bool = False) -> str:
    """Render *source* with the sandbox engine, never raising.

    A template with a syntax error falls back to its own raw source: the
    operator then sees exactly what is broken instead of an empty message.
    """
    if not source:
        return ""
    try:
        rendered = _sandbox_engine().from_string(source).render(
            Context(context, autoescape=True)
        )
    except TemplateSyntaxError:
        rendered = source
    except Exception:  # noqa: BLE001 - a bad template must never break delivery
        rendered = source
    rendered = " ".join(rendered.split()) if single_line else rendered.strip()
    return rendered[:limit]


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
class NotificationQuerySet(SoftDeleteQuerySet):
    def for_user(self, user):
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(recipient=user)

    def unread(self):
        return self.filter(is_read=False)

    def read(self):
        return self.filter(is_read=True)

    def in_category(self, category: str):
        return self.filter(category=category) if category else self

    def about(self, instance) -> NotificationQuerySet:
        """Notifications whose soft reference points at *instance*."""
        meta = getattr(instance, "_meta", None)
        if meta is None or getattr(instance, "pk", None) is None:
            return self.none()
        return self.filter(
            related_object_type=f"{meta.app_label}.{meta.model_name}",
            related_object_id=instance.pk,
        )


class NotificationManager(models.Manager.from_queryset(NotificationQuerySet)):
    """Default manager — hides soft-deleted rows, exactly like ``BaseModel``."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllNotificationsManager(models.Manager.from_queryset(NotificationQuerySet)):
    """Escape hatch that includes soft-deleted rows."""


class Notification(BaseModel):
    """One message addressed to one user."""

    #: Convenience aliases so callers can write ``Notification.Category.BOOKING``.
    Category = NotificationCategory
    Level = NotificationLevel

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("recipient"),
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    category = models.CharField(
        _("category"),
        max_length=20,
        choices=NotificationCategory.choices,
        default=NotificationCategory.SYSTEM,
        db_index=True,
    )
    level = models.CharField(
        _("level"),
        max_length=10,
        choices=NotificationLevel.choices,
        default=NotificationLevel.INFO,
        db_index=True,
    )
    title = models.CharField(_("title"), max_length=MAX_RENDERED_TITLE)
    body = models.TextField(_("message"), blank=True)
    link_url = models.CharField(
        _("link"),
        max_length=500,
        blank=True,
        help_text=_("Relative path opened when the notification is clicked."),
    )

    is_read = models.BooleanField(_("read"), default=False, db_index=True)
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)

    is_emailed = models.BooleanField(_("e-mailed"), default=False)
    emailed_at = models.DateTimeField(_("e-mailed at"), null=True, blank=True)

    # --- soft reference to the record that caused this notification --------
    related_object_type = models.CharField(
        _("related record type"),
        max_length=100,
        blank=True,
        help_text=_("Lowercase \"app_label.model\" label — deliberately not a foreign key."),
    )
    related_object_id = models.PositiveIntegerField(
        _("related record id"), null=True, blank=True
    )

    objects = NotificationManager()
    all_objects = AllNotificationsManager()

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            models.Index(fields=["category", "-created_at"]),
            models.Index(fields=["related_object_type", "related_object_id"]),
        ]

    def __str__(self) -> str:
        return self.title

    # -- presentation ------------------------------------------------------
    @property
    def icon_name(self) -> str:
        return CATEGORY_ICONS.get(self.category, "bell")

    @property
    def badge_color(self) -> str:
        return LEVEL_BADGE_COLORS.get(self.level, "slate")

    @property
    def related_label(self) -> str:
        """``"bookings.booking #42"`` or an empty string."""
        if not self.related_object_type or not self.related_object_id:
            return ""
        return f"{self.related_object_type} #{self.related_object_id}"

    # -- state -------------------------------------------------------------
    def mark_read(self, *, when=None) -> bool:
        """Flag as read. Returns ``True`` when something actually changed."""
        if self.is_read:
            return False
        self.is_read = True
        self.read_at = when or timezone.now()
        self.save(update_fields=["is_read", "read_at", "updated_at"])
        return True

    def mark_unread(self) -> bool:
        if not self.is_read:
            return False
        self.is_read = False
        self.read_at = None
        self.save(update_fields=["is_read", "read_at", "updated_at"])
        return True

    def mark_emailed(self, *, when=None) -> None:
        self.is_emailed = True
        self.emailed_at = when or timezone.now()
        self.save(update_fields=["is_emailed", "emailed_at", "updated_at"])

    def set_related(self, instance) -> None:
        """Store the soft reference to *instance* (no-op for unsaved objects)."""
        meta = getattr(instance, "_meta", None)
        if meta is None or getattr(instance, "pk", None) is None:
            self.related_object_type = ""
            self.related_object_id = None
            return
        self.related_object_type = f"{meta.app_label}.{meta.model_name}"[:100]
        try:
            self.related_object_id = int(instance.pk)
        except (TypeError, ValueError):
            # Non-integer primary keys (UUID) are simply not referenced.
            self.related_object_type = ""
            self.related_object_id = None


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------
class NotificationTemplate(TimeStampedModel):
    """A reusable bilingual message body, editable without a deployment."""

    code = models.SlugField(
        _("code"),
        max_length=80,
        unique=True,
        help_text=_("Stable identifier used by the code, e.g. \"booking-confirmed\"."),
    )
    category = models.CharField(
        _("category"),
        max_length=20,
        choices=NotificationCategory.choices,
        default=NotificationCategory.SYSTEM,
        db_index=True,
    )
    level = models.CharField(
        _("level"),
        max_length=10,
        choices=NotificationLevel.choices,
        default=NotificationLevel.INFO,
    )
    title_en = models.CharField(_("title (EN)"), max_length=MAX_RENDERED_TITLE)
    title_tr = models.CharField(_("title (TR)"), max_length=MAX_RENDERED_TITLE, blank=True)
    body_en = models.TextField(_("message (EN)"), blank=True)
    body_tr = models.TextField(_("message (TR)"), blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("notification template")
        verbose_name_plural = _("notification templates")
        ordering = ["category", "code"]

    def __str__(self) -> str:
        return self.code

    def render(self, language: str | None, context: dict | None = None) -> tuple[str, str]:
        """Return ``(title, body)`` for *language*, substituting *context*.

        Falls back to English whenever the requested language has no text, so a
        half-translated template still produces a usable message.
        """
        code = (language or "").strip().lower()[:2]
        title_source = (self.title_tr if code == "tr" else "") or self.title_en
        body_source = (self.body_tr if code == "tr" else "") or self.body_en

        safe = restrict_context(context)
        return (
            render_sandboxed(title_source, safe, MAX_RENDERED_TITLE, single_line=True),
            render_sandboxed(body_source, safe, MAX_RENDERED_BODY),
        )


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
class NotificationPreference(TimeStampedModel):
    """Per-user delivery rules."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    in_app_enabled = models.BooleanField(
        _("in-app notifications"),
        default=True,
        help_text=_("Turn off to stop new entries appearing in the bell menu."),
    )
    email_enabled = models.BooleanField(
        _("e-mail notifications"),
        default=True,
        help_text=_("Warnings and errors are also sent to your e-mail address."),
    )
    categories_muted = models.JSONField(
        _("muted categories"),
        default=list,
        blank=True,
        help_text=_("Categories you never want to hear about, on any channel."),
    )
    quiet_hours_start = models.TimeField(
        _("quiet hours start"),
        null=True,
        blank=True,
        help_text=_("No e-mail is sent between these two times. In-app entries still arrive."),
    )
    quiet_hours_end = models.TimeField(_("quiet hours end"), null=True, blank=True)

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self) -> str:
        return f"{self.user} — {_('notification preferences')}"

    # -- helpers -----------------------------------------------------------
    @property
    def muted_categories(self) -> set[str]:
        raw = self.categories_muted or []
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {str(value) for value in raw}

    @property
    def has_quiet_hours(self) -> bool:
        return bool(
            self.quiet_hours_start
            and self.quiet_hours_end
            and self.quiet_hours_start != self.quiet_hours_end
        )

    def is_quiet_at(self, moment=None) -> bool:
        """Is *moment* (default: now, in the school's timezone) inside quiet hours?"""
        if not self.has_quiet_hours:
            return False
        current = (moment or timezone.localtime()).time()
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start < end:
            return start <= current < end
        # Window wraps past midnight, e.g. 22:00 -> 07:00.
        return current >= start or current < end

    def allows(self, category: str, channel: str = NotificationChannel.IN_APP) -> bool:
        """May a *category* message be delivered on *channel* right now?"""
        if category and str(category) in self.muted_categories:
            return False
        if channel == NotificationChannel.IN_APP:
            return bool(self.in_app_enabled)
        if channel == NotificationChannel.EMAIL:
            if not self.email_enabled:
                return False
            return not self.is_quiet_at()
        return False

    # -- construction ------------------------------------------------------
    @classmethod
    def for_user(cls, user, *, create: bool = False) -> NotificationPreference:
        """Return *user*'s preferences.

        ``create=False`` (the default) returns an **unsaved** default instance
        when the user has none, so read paths — including every
        :func:`~apps.notifications.services.notify` call — stay free of writes.
        """
        existing = cls.objects.filter(user=user).first()
        if existing is not None:
            return existing
        if create:
            return cls.objects.create(user=user)
        return cls(user=user)
