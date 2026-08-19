"""The single row that records how far first-run setup has got.

``OnboardingState`` is a singleton by convention rather than by a database
constraint: :meth:`get_state` always returns the lowest-pk row and creates it if
it is missing. A hard constraint would turn a stray second row — created by a
fixture, a restored backup or a race — into a 500 on every user's dashboard,
which is a poor trade for a preferences table.

The fields mirror the wizard's steps so a half-finished setup survives a closed
browser, and ``completed_steps`` records which steps were answered rather than
skipped.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.validators import (
    phone_validator,
    validate_latitude,
    validate_longitude,
)

#: Currencies the school can be run in. The symbol table in ``settings.SCHOOL``
#: knows these four; anything else would display as a bare code.
CURRENCY_CHOICES: tuple[tuple[str, object], ...] = (
    ("TRY", _("Turkish lira (₺)")),
    ("EUR", _("Euro (€)")),
    ("USD", _("US dollar ($)")),
    ("GBP", _("Pound sterling (£)")),
)

#: Time zones a Turkish- or European-based surf school realistically runs in.
#: The field accepts any IANA name; these are only the quick choices.
COMMON_TIMEZONES: tuple[str, ...] = (
    "Europe/Istanbul",
    "Europe/Lisbon",
    "Europe/Madrid",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/London",
    "Atlantic/Canary",
    "Africa/Casablanca",
    "UTC",
)


class OnboardingState(TimeStampedModel):
    """Everything the setup wizard has been told so far."""

    #: Ordered step keys. The wizard, the progress bar and the URL all use these.
    STEPS: tuple[tuple[str, object], ...] = (
        ("welcome", _("Welcome")),
        ("business", _("Business information")),
        ("language", _("Language")),
        ("currency", _("Currency")),
        ("location", _("Location & surf spots")),
        ("staff", _("Staff")),
        ("ai", _("AI setup")),
        ("backup", _("Backup setup")),
        ("finish", _("Finish")),
    )

    is_completed = models.BooleanField(_("completed"), default=False, db_index=True)
    is_dismissed = models.BooleanField(
        _("dismissed"),
        default=False,
        help_text=_("The operator chose to skip setup; hide the dashboard banner."),
    )
    current_step = models.PositiveIntegerField(
        _("current step"),
        default=1,
        help_text=_("1-based index into STEPS of the step last shown."),
    )
    completed_steps = models.JSONField(
        _("completed steps"),
        default=list,
        blank=True,
        help_text=_("Keys of the steps that were answered rather than skipped."),
    )

    # --- business information ---------------------------------------------
    school_name = models.CharField(_("school name"), max_length=150, blank=True)
    contact_email = models.EmailField(_("contact e-mail"), blank=True)
    contact_phone = models.CharField(
        _("contact phone"), max_length=25, blank=True, validators=[phone_validator]
    )
    address = models.CharField(_("address"), max_length=250, blank=True)

    # --- preferences -------------------------------------------------------
    default_language = models.CharField(_("default language"), max_length=5, default="tr")
    currency = models.CharField(
        _("currency"), max_length=3, default="TRY", choices=CURRENCY_CHOICES
    )
    timezone = models.CharField(
        _("time zone"),
        max_length=64,
        default="Europe/Istanbul",
        help_text=_("IANA name, e.g. Europe/Istanbul."),
    )

    # --- location ----------------------------------------------------------
    primary_spot_name = models.CharField(_("primary surf spot"), max_length=120, blank=True)
    latitude = models.FloatField(
        _("latitude"), null=True, blank=True, validators=[validate_latitude]
    )
    longitude = models.FloatField(
        _("longitude"), null=True, blank=True, validators=[validate_longitude]
    )
    beach_facing_deg = models.FloatField(
        _("beach facing (°)"),
        null=True,
        blank=True,
        help_text=_(
            "Compass direction you look towards when standing on the sand facing the "
            "water. Wind quality is classified against it."
        ),
    )

    # --- flags set by later steps -----------------------------------------
    staff_invited = models.BooleanField(_("staff added"), default=False)
    ai_configured = models.BooleanField(_("AI configured"), default=False)
    backup_configured = models.BooleanField(_("backup configured"), default=False)

    #: Set once the wizard has actually created the SurfSpot / settings rows,
    #: so re-running Finish is idempotent rather than duplicating records.
    records_created = models.BooleanField(_("records created"), default=False)

    # --- audit -------------------------------------------------------------
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("started by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_sessions",
    )
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("onboarding state")
        verbose_name_plural = _("onboarding state")
        ordering = ["pk"]

    def __str__(self) -> str:
        if self.is_completed:
            return str(_("Setup completed"))
        return str(_("Setup in progress — %(step)s")) % {"step": self.current_slug}

    # -- singleton access ---------------------------------------------------
    @classmethod
    def get_state(cls) -> OnboardingState:
        """Return the one state row, seeding it from settings on first access."""
        state = cls.objects.order_by("pk").first()
        if state is None:
            from django.conf import settings as django_settings

            state = cls.objects.create(
                school_name=django_settings.SCHOOL["NAME"],
                currency=django_settings.SCHOOL["CURRENCY"],
                default_language=django_settings.LANGUAGE_CODE,
                timezone=django_settings.TIME_ZONE,
                primary_spot_name=django_settings.SCHOOL["DEFAULT_SPOT_NAME"],
                latitude=django_settings.SCHOOL["DEFAULT_LATITUDE"],
                longitude=django_settings.SCHOOL["DEFAULT_LONGITUDE"],
            )
        return state

    @classmethod
    def is_setup_complete(cls) -> bool:
        """Cheap read used by the dashboard banner — never creates a row."""
        state = cls.objects.order_by("pk").only("is_completed").first()
        return bool(state and state.is_completed)

    # -- progress -----------------------------------------------------------
    @classmethod
    def step_number(cls, key: str) -> int:
        """1-based position of *key* in :attr:`STEPS` (1 when unknown)."""
        keys = [slug for slug, _label in cls.STEPS]
        return keys.index(key) + 1 if key in keys else 1

    @classmethod
    def step_slug(cls, number) -> str:
        """The slug at 1-based *number*, clamped into range."""
        keys = [slug for slug, _label in cls.STEPS]
        try:
            index = int(number) - 1
        except (TypeError, ValueError):
            return keys[0]
        return keys[min(max(index, 0), len(keys) - 1)]

    @property
    def current_slug(self) -> str:
        return self.step_slug(self.current_step)

    @property
    def step_keys(self) -> list[str]:
        return [key for key, _label in self.STEPS]

    @property
    def answered_steps(self) -> list[str]:
        return [str(key) for key in (self.completed_steps or [])]

    def has_answered(self, key: str) -> bool:
        return key in self.answered_steps

    @property
    def percent_complete(self) -> int:
        total = len(self.STEPS)
        done = len([key for key in self.answered_steps if key in self.step_keys])
        return int(round((done / total) * 100)) if total else 0

    def mark_step_complete(self, key: str) -> None:
        """Record *key* as answered. Does not save."""
        answered = self.answered_steps
        if key not in answered:
            answered.append(key)
        self.completed_steps = answered

    def mark_answered(self, key: str) -> None:
        """Alias of :meth:`mark_step_complete`, used by the services layer."""
        self.mark_step_complete(key)

    def mark_unanswered(self, key: str) -> None:
        """Forget an answer, used when a step is skipped after being filled."""
        self.completed_steps = [item for item in self.answered_steps if item != key]

    def next_step(self, key: str) -> str | None:
        keys = self.step_keys
        try:
            index = keys.index(key)
        except ValueError:
            return keys[0]
        return keys[index + 1] if index + 1 < len(keys) else None

    def previous_step(self, key: str) -> str | None:
        keys = self.step_keys
        try:
            index = keys.index(key)
        except ValueError:
            return None
        return keys[index - 1] if index > 0 else None

    # -- derived ------------------------------------------------------------
    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def can_create_spot(self) -> bool:
        """A surf spot needs at least a name and coordinates to be useful."""
        return bool(self.primary_spot_name and self.has_coordinates)

    def complete(self) -> None:
        self.is_completed = True
        self.current_step = len(self.STEPS)
        self.completed_at = django_timezone.now()
        self.mark_step_complete("finish")
        self.save()
