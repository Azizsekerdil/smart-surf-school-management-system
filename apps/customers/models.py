"""Customer models.

Design notes
------------
* ``customer_code`` is the human-facing identifier printed on receipts and read
  out on the phone. It is generated once and never changes, so a merge keeps the
  surviving customer's code.
* ``email`` and ``phone`` are normalised on save (lower-cased e-mail, digits-only
  phone with an optional leading ``+``). Duplicate detection depends on this:
  ``+90 555 111 22 33`` and ``+905551112233`` are the same human being.
* ``lifetime_value`` / ``total_bookings`` / ``first_visit_date`` /
  ``last_visit_date`` are denormalised roll-ups. They are refreshed by
  :func:`apps.customers.services.recalculate_lifetime_value`; nothing reads them
  as the source of truth for money.
"""

from __future__ import annotations

import re
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import BookingSource, Gender, Language
from apps.core.models import AddressMixin, BaseModel, money_field
from apps.core.utils import next_sequential_code
from apps.core.validators import phone_validator, validate_image_upload

#: Prefix and width of the generated customer code, e.g. ``CUS00001``.
CUSTOMER_CODE_PREFIX = "CUS"
CUSTOMER_CODE_WIDTH = 5

#: Age below which a customer is a minor and stricter rules apply.
AGE_OF_MAJORITY = 18

_PHONE_STRIP = re.compile(r"[^0-9+]")


def normalise_phone(value: str | None) -> str:
    """Return *value* as ``+<digits>`` / ``<digits>``.

    Keeping one canonical form in the column is what makes the duplicate finder
    and the "customer already exists" check at the counter actually work.
    """
    if not value:
        return ""
    cleaned = _PHONE_STRIP.sub("", str(value).strip())
    if not cleaned:
        return ""
    # A '+' is only meaningful as the very first character.
    leading_plus = cleaned.startswith("+")
    digits = cleaned.replace("+", "")
    return f"+{digits}" if leading_plus else digits


class CustomerQuerySet(models.QuerySet):
    """Read helpers reused by views, selectors and the API."""

    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)

    def with_bookings(self):
        return self.filter(total_bookings__gt=0)

    def without_bookings(self):
        return self.filter(total_bookings=0)

    def minors(self):
        """Customers under 18 today (evaluated against the stored birth date)."""
        cutoff = _minor_cutoff_date()
        return self.filter(birth_date__isnull=False, birth_date__gt=cutoff)

    def adults(self):
        cutoff = _minor_cutoff_date()
        return self.filter(Q(birth_date__isnull=True) | Q(birth_date__lte=cutoff))

    def search(self, term: str):
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
            | Q(phone__icontains=normalise_phone(term) or term)
            | Q(customer_code__icontains=term)
        )


def _minor_cutoff_date() -> date:
    """The birth date at which someone turns exactly 18 today."""
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - AGE_OF_MAJORITY)
    except ValueError:  # 29 February
        return today.replace(year=today.year - AGE_OF_MAJORITY, day=28)


class CustomerManager(models.Manager.from_queryset(CustomerQuerySet)):
    """Default manager: hides soft-deleted rows (see ``SoftDeleteManager``)."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class CustomerAllObjectsManager(models.Manager.from_queryset(CustomerQuerySet)):
    """Escape hatch used by merges, audits and code generation."""


class Customer(BaseModel, AddressMixin):
    """A person the school sells to: bookings, rentals, camps and shop sales."""

    customer_code = models.CharField(
        _("customer code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Generated automatically, e.g. CUS00001."),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("login account"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_profile",
        help_text=_("Linked when the customer is given access to the portal."),
    )

    # --- identity ---------------------------------------------------------
    first_name = models.CharField(_("first name"), max_length=80)
    last_name = models.CharField(_("last name"), max_length=80)
    email = models.EmailField(_("e-mail"), blank=True, db_index=True)
    phone = models.CharField(
        _("phone"),
        max_length=32,
        blank=True,
        db_index=True,
        validators=[phone_validator],
        help_text=_("Stored in international form, e.g. +905551234567."),
    )
    photo = models.ImageField(
        _("photo"),
        upload_to="customers/photos/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    birth_date = models.DateField(_("date of birth"), null=True, blank=True)
    gender = models.CharField(_("gender"), max_length=12, choices=Gender.choices, blank=True)
    nationality = models.CharField(
        _("nationality"), max_length=2, blank=True, help_text=_("ISO 3166-1 alpha-2, e.g. TR")
    )
    preferred_language = models.CharField(
        _("preferred language"),
        max_length=5,
        choices=Language.choices,
        default=Language.ENGLISH,
    )

    # --- emergency --------------------------------------------------------
    emergency_contact_name = models.CharField(
        _("emergency contact"), max_length=120, blank=True
    )
    emergency_contact_phone = models.CharField(
        _("emergency contact phone"),
        max_length=32,
        blank=True,
        validators=[phone_validator],
    )
    emergency_contact_relation = models.CharField(
        _("relationship"),
        max_length=60,
        blank=True,
        help_text=_("e.g. Mother, Partner, Friend"),
    )

    # --- commercial -------------------------------------------------------
    source = models.CharField(
        _("source"),
        max_length=16,
        choices=BookingSource.choices,
        default=BookingSource.WALK_IN,
        db_index=True,
    )
    tags = models.ManyToManyField(
        "core.Tag",
        verbose_name=_("tags"),
        blank=True,
        through="customers.CustomerTag",
        related_name="customers",
    )
    marketing_consent = models.BooleanField(
        _("marketing consent"),
        default=False,
        help_text=_("Explicit opt-in for campaigns. Without it we never contact them."),
    )
    marketing_consent_at = models.DateTimeField(
        _("consent given at"), null=True, blank=True, editable=False
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    # --- roll-ups (denormalised, refreshed by services) -------------------
    first_visit_date = models.DateField(_("first visit"), null=True, blank=True)
    last_visit_date = models.DateField(_("last visit"), null=True, blank=True, db_index=True)
    lifetime_value = money_field(_("lifetime value"))
    total_bookings = models.PositiveIntegerField(_("total bookings"), default=0)

    notes = models.TextField(_("notes"), blank=True)

    objects = CustomerManager()
    all_objects = CustomerAllObjectsManager()

    class Meta:
        verbose_name = _("customer")
        verbose_name_plural = _("customers")
        ordering = ["last_name", "first_name"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["is_active", "source"]),
            models.Index(fields=["email", "is_deleted"]),
            models.Index(fields=["phone", "last_name"]),
            models.Index(fields=["-last_visit_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.customer_code})" if self.customer_code else self.full_name

    # ------------------------------------------------------------------ data
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.birth_date and self.birth_date > timezone.localdate():
            errors.setdefault("birth_date", []).append(
                ValidationError(_("The date of birth cannot be in the future."))
            )
        if self.birth_date and self.birth_date.year < 1900:
            errors.setdefault("birth_date", []).append(
                ValidationError(_("Please check the date of birth."))
            )

        if self.nationality and not self.nationality.isalpha():
            errors.setdefault("nationality", []).append(
                ValidationError(_("Use the two-letter country code, e.g. TR."))
            )

        # A customer we cannot reach is a customer we cannot serve.
        if not (self.email or "").strip() and not (self.phone or "").strip():
            message = _("Enter at least an e-mail address or a phone number.")
            errors.setdefault("email", []).append(ValidationError(message))

        # Minors must have a reachable adult on file — this is a safety rule,
        # not paperwork: it is the number the instructor calls from the beach.
        if self.is_minor:
            if not (self.emergency_contact_name or "").strip():
                errors.setdefault("emergency_contact_name", []).append(
                    ValidationError(
                        _("An emergency contact is required for customers under 18.")
                    )
                )
            if not (self.emergency_contact_phone or "").strip():
                errors.setdefault("emergency_contact_phone", []).append(
                    ValidationError(
                        _("An emergency contact phone is required for customers under 18.")
                    )
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.first_name = (self.first_name or "").strip()
        self.last_name = (self.last_name or "").strip()
        self.email = (self.email or "").strip().lower()
        self.phone = normalise_phone(self.phone)
        self.emergency_contact_phone = normalise_phone(self.emergency_contact_phone)
        self.nationality = (self.nationality or "").strip().upper()
        if self.marketing_consent and self.marketing_consent_at is None:
            self.marketing_consent_at = timezone.now()
        if not self.marketing_consent:
            self.marketing_consent_at = None
        if not self.customer_code:
            self.customer_code = next_sequential_code(
                Customer, "customer_code", CUSTOMER_CODE_PREFIX, CUSTOMER_CODE_WIDTH
            )
        # ``update_fields`` callers must not lose the normalisation above.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = sorted(
                set(update_fields) | {"customer_code", "email", "phone", "marketing_consent_at"}
            )
        super().save(*args, **kwargs)

    # ------------------------------------------------------------ properties
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        first = (self.first_name or " ")[:1].upper()
        last = (self.last_name or " ")[:1].upper()
        return f"{first}{last}".strip() or "?"

    @property
    def age(self) -> int | None:
        """Completed years of age, or ``None`` when no birth date is recorded."""
        if not self.birth_date:
            return None
        today = timezone.localdate()
        years = today.year - self.birth_date.year
        if (today.month, today.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return max(years, 0)

    @property
    def is_minor(self) -> bool:
        age = self.age
        return age is not None and age < AGE_OF_MAJORITY

    @property
    def has_emergency_contact(self) -> bool:
        return bool(
            (self.emergency_contact_name or "").strip()
            and (self.emergency_contact_phone or "").strip()
        )

    # --------------------------------------------------------------- methods
    def has_valid_waiver(self) -> bool:
        """True when a non-expired signed waiver is attached to this customer.

        Waivers are stored as :class:`apps.core.models.Document` rows with
        ``category=WAIVER``. A document without an expiry date never expires.
        """
        if not self.pk:
            return False

        from django.contrib.contenttypes.models import ContentType

        from apps.core.models import Document

        content_type = ContentType.objects.get_for_model(self.__class__)
        today = timezone.localdate()
        return (
            Document.objects.filter(
                content_type=content_type,
                object_id=self.pk,
                category=Document.Category.WAIVER,
            )
            .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
            .exists()
        )

    def waiver_document(self):
        """Return the most recent valid waiver document, or ``None``."""
        if not self.pk:
            return None

        from django.contrib.contenttypes.models import ContentType

        from apps.core.models import Document

        content_type = ContentType.objects.get_for_model(self.__class__)
        today = timezone.localdate()
        return (
            Document.objects.filter(
                content_type=content_type,
                object_id=self.pk,
                category=Document.Category.WAIVER,
            )
            .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
            .order_by("-created_at")
            .first()
        )


class CustomerTag(models.Model):
    """Through table for :attr:`Customer.tags`.

    Explicit so we can record *who* labelled a customer and *when* — segment
    membership drives marketing, and marketing decisions need provenance.
    """

    customer = models.ForeignKey(
        Customer,
        verbose_name=_("customer"),
        on_delete=models.CASCADE,
        related_name="tag_links",
    )
    tag = models.ForeignKey(
        "core.Tag",
        verbose_name=_("tag"),
        on_delete=models.CASCADE,
        related_name="customer_links",
    )
    added_at = models.DateTimeField(_("added at"), auto_now_add=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("added by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_tag_links",
    )

    class Meta:
        verbose_name = _("customer tag")
        verbose_name_plural = _("customer tags")
        ordering = ["tag__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "tag"], name="customers_customertag_unique"
            )
        ]
        indexes = [models.Index(fields=["tag", "customer"])]

    def __str__(self) -> str:
        return f"{self.customer_id} · {self.tag_id}"
