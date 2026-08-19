"""Abstract base models and small shared concrete models.

Contract for every other app
----------------------------
* Business entities inherit from :class:`BaseModel` (UUID public id, created/
  updated timestamps, created_by/updated_by, soft delete).
* Money is always ``DecimalField(max_digits=12, decimal_places=2)`` — never a
  float. Use :func:`money_field`.
* Soft-deleted rows are hidden from ``.objects`` and visible via
  ``.all_objects``. ``Meta.base_manager_name`` is set so related-object
  descriptors keep working.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------
MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2
ZERO = Decimal("0.00")


def money_field(verbose_name=None, default=ZERO, **kwargs) -> models.DecimalField:
    """Return the project-standard monetary field.

    Using one helper guarantees every amount in the system has identical
    precision, which keeps sums and comparisons exact.
    """
    kwargs.setdefault("max_digits", MONEY_MAX_DIGITS)
    kwargs.setdefault("decimal_places", MONEY_DECIMAL_PLACES)
    kwargs.setdefault("default", default)
    return models.DecimalField(verbose_name=verbose_name, **kwargs)


def percent_field(verbose_name=None, default=Decimal("0.00"), **kwargs) -> models.DecimalField:
    kwargs.setdefault("max_digits", 5)
    kwargs.setdefault("decimal_places", 2)
    kwargs.setdefault("default", default)
    return models.DecimalField(verbose_name=verbose_name, **kwargs)


# ---------------------------------------------------------------------------
# Querysets & managers
# ---------------------------------------------------------------------------
class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that understands the soft-delete flag."""

    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)

    def delete(self):
        """Soft-delete every row in the queryset."""
        return self.update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        """Permanently remove rows. Only ever called from explicit admin flows."""
        return super().delete()

    def restore(self):
        return self.update(is_deleted=False, deleted_at=None)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager: hides soft-deleted rows."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Escape hatch manager: includes soft-deleted rows."""


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Adds a non-guessable public identifier.

    The integer primary key stays for efficient joins; ``public_id`` is what we
    expose in URLs and QR codes so record counts are not leaked.
    """

    public_id = models.UUIDField(
        _("public id"), default=uuid.uuid4, editable=False, unique=True, db_index=True
    )

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(_("deleted"), default=False, db_index=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    def delete(self, using=None, keep_parents=False, hard: bool = False):
        if hard:
            return super().delete(using=using, keep_parents=keep_parents)
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        return (0, {})

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class AuthoredModel(models.Model):
    """Tracks which user created and last changed a record."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("created by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("updated by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel, AuthoredModel, SoftDeleteModel):
    """The standard base for every business entity in the system."""

    class Meta:
        abstract = True
        base_manager_name = "all_objects"


class AddressMixin(models.Model):
    address_line1 = models.CharField(_("address line 1"), max_length=200, blank=True)
    address_line2 = models.CharField(_("address line 2"), max_length=200, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    state = models.CharField(_("state / region"), max_length=100, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    country = models.CharField(_("country"), max_length=2, blank=True, help_text=_("ISO 3166-1 alpha-2"))

    class Meta:
        abstract = True

    @property
    def full_address(self) -> str:
        parts = [
            self.address_line1,
            self.address_line2,
            self.postal_code,
            self.city,
            self.state,
            self.country,
        ]
        return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Shared concrete models
# ---------------------------------------------------------------------------
class Tag(TimeStampedModel):
    """Free-form label attachable to customers, equipment, lessons, …"""

    name = models.CharField(_("name"), max_length=60, unique=True)
    slug = models.SlugField(_("slug"), max_length=60, unique=True)
    color = models.CharField(
        _("color"),
        max_length=7,
        default="#0ea5e9",
        help_text=_("Hex colour used for the badge, e.g. #0ea5e9"),
    )
    description = models.CharField(_("description"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("tag")
        verbose_name_plural = _("tags")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Note(BaseModel):
    """A note attached to any record via a generic relation."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    body = models.TextField(_("note"))
    is_pinned = models.BooleanField(_("pinned"), default=False)
    is_internal = models.BooleanField(
        _("internal only"),
        default=True,
        help_text=_("Internal notes are never shown to customers."),
    )

    class Meta:
        verbose_name = _("note")
        verbose_name_plural = _("notes")
        ordering = ["-is_pinned", "-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return self.body[:60]


class Document(BaseModel):
    """A file attached to any record (waiver, certificate, invoice, photo …)."""

    class Category(models.TextChoices):
        WAIVER = "waiver", _("Waiver / consent form")
        CERTIFICATE = "certificate", _("Certificate")
        IDENTITY = "identity", _("Identity document")
        MEDICAL = "medical", _("Medical information")
        INSURANCE = "insurance", _("Insurance")
        INVOICE = "invoice", _("Invoice")
        CONTRACT = "contract", _("Contract")
        PHOTO = "photo", _("Photo")
        MANUAL = "manual", _("Manual / procedure")
        OTHER = "other", _("Other")

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    title = models.CharField(_("title"), max_length=200)
    category = models.CharField(
        _("category"), max_length=20, choices=Category.choices, default=Category.OTHER
    )
    file = models.FileField(_("file"), upload_to="documents/%Y/%m/")
    content_type_hint = models.CharField(_("MIME type"), max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(_("size (bytes)"), default=0)
    expires_on = models.DateField(
        _("expires on"), null=True, blank=True, help_text=_("Used for certificates and insurance.")
    )
    is_confidential = models.BooleanField(_("confidential"), default=False)

    class Meta:
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return self.title

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < timezone.localdate())

    def save(self, *args, **kwargs):
        if self.file and not self.size_bytes:
            try:
                self.size_bytes = self.file.size
            except (OSError, ValueError):
                self.size_bytes = 0
        super().save(*args, **kwargs)


class SystemSetting(TimeStampedModel):
    """Runtime-editable key/value configuration.

    Used by the onboarding wizard and the AI Control Center so an operator can
    change behaviour without editing ``.env`` — but secrets still come from the
    environment, never from here in plain text.
    """

    class ValueType(models.TextChoices):
        STRING = "string", _("Text")
        INTEGER = "integer", _("Integer")
        DECIMAL = "decimal", _("Decimal")
        BOOLEAN = "boolean", _("Yes / No")
        JSON = "json", _("JSON")

    key = models.CharField(_("key"), max_length=100, unique=True)
    value = models.TextField(_("value"), blank=True)
    value_type = models.CharField(
        _("type"), max_length=10, choices=ValueType.choices, default=ValueType.STRING
    )
    group = models.CharField(_("group"), max_length=50, default="general", db_index=True)
    label_en = models.CharField(_("label (EN)"), max_length=150, blank=True)
    label_tr = models.CharField(_("label (TR)"), max_length=150, blank=True)
    is_secret = models.BooleanField(
        _("secret"),
        default=False,
        help_text=_("Secret values are masked in the UI and never logged."),
    )

    class Meta:
        verbose_name = _("system setting")
        verbose_name_plural = _("system settings")
        ordering = ["group", "key"]

    def __str__(self) -> str:
        return self.key

    def typed_value(self):
        """Return ``value`` coerced to its declared type."""
        import json

        raw = self.value
        if self.value_type == self.ValueType.INTEGER:
            return int(raw or 0)
        if self.value_type == self.ValueType.DECIMAL:
            return Decimal(raw or "0")
        if self.value_type == self.ValueType.BOOLEAN:
            return str(raw).strip().lower() in {"1", "true", "yes", "on", "evet"}
        if self.value_type == self.ValueType.JSON:
            try:
                return json.loads(raw or "null")
            except json.JSONDecodeError:
                return None
        return raw

    @classmethod
    def get(cls, key: str, default=None):
        try:
            return cls.objects.get(key=key).typed_value()
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key: str, value, value_type: str = ValueType.STRING, group: str = "general"):
        import json

        if value_type == cls.ValueType.JSON:
            value = json.dumps(value, ensure_ascii=False)
        obj, _created = cls.objects.update_or_create(
            key=key,
            defaults={"value": str(value), "value_type": value_type, "group": group},
        )
        return obj
