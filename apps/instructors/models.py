"""Instructor domain models.

Design notes
------------
* **Certification is a collection, not a flag.** A working surf coach holds four
  or five independently expiring credentials (coaching award, water-safety /
  rescue award, first aid, safeguarding …). Modelling them as booleans on the
  instructor makes expiry untrackable, so each credential is its own row with
  its own ``expires_on``.
* **Availability is a weekly pattern with a validity window.** Summer staff work
  a different pattern from winter staff, so a slot may carry ``valid_from`` /
  ``valid_until`` instead of being duplicated every season.
* **Absence is explicit and approved.** An unapproved time-off request must not
  silently remove an instructor from the booking pool — that is how double
  bookings and unstaffed lessons happen.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel, level_rank
from apps.core.models import BaseModel, TimeStampedModel, money_field, percent_field
from apps.core.utils import next_sequential_code
from apps.core.validators import (
    phone_validator,
    validate_document_upload,
    validate_image_upload,
)

#: A certification inside this window is reported as "expiring soon". 60 days is
#: early enough to book a first-aid requalification course, which is the
#: credential with the hardest renewal cliff.
EXPIRY_WARNING_DAYS = 60

#: The highest number of students any single instructor may take, whatever the
#: level. Mirrors the safety ratio table in ``apps.core.enums``.
ABSOLUTE_MAX_STUDENTS_PER_INSTRUCTOR = 10


class Instructor(BaseModel):
    """A coach who can be assigned to lessons, camps and safety duties."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user account"),
        on_delete=models.PROTECT,
        related_name="instructor_profile",
        help_text=_("The login this instructor uses. One profile per account."),
    )
    instructor_code = models.CharField(
        _("instructor code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Generated automatically, e.g. INS0001."),
    )
    bio = models.TextField(
        _("biography"),
        blank=True,
        help_text=_("Shown on the public profile and on lesson confirmations."),
    )
    photo = models.ImageField(
        _("photo"),
        upload_to="instructors/photos/%Y/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    specialties = models.JSONField(
        _("specialties"),
        default=list,
        blank=True,
        help_text=_("Free-form list, e.g. longboard, kids, competition coaching."),
    )
    languages = models.JSONField(
        _("languages spoken"),
        default=list,
        blank=True,
        help_text=_("ISO 639-1 codes, e.g. tr, en, de."),
    )
    max_level_taught = models.CharField(
        _("highest level taught"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.BEGINNER,
        db_index=True,
    )
    max_students_per_lesson = models.PositiveSmallIntegerField(
        _("maximum students per lesson"),
        default=8,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(ABSOLUTE_MAX_STUDENTS_PER_INSTRUCTOR),
        ],
        help_text=_(
            "Personal ceiling. The effective limit is the lower of this value "
            "and the safety ratio for the group's level."
        ),
    )
    hourly_rate = money_field(_("hourly rate"))
    commission_percent = percent_field(
        _("commission %"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    hire_date = models.DateField(_("hire date"), null=True, blank=True, db_index=True)
    is_active = models.BooleanField(
        _("active"),
        default=True,
        db_index=True,
        help_text=_("Inactive instructors keep their history but cannot be assigned."),
    )
    is_available_for_booking = models.BooleanField(
        _("open for bookings"),
        default=True,
        db_index=True,
        help_text=_("Turn off to keep an active instructor out of the booking pool."),
    )
    rating_average = models.DecimalField(
        _("average rating"),
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("5"))],
    )
    rating_count = models.PositiveIntegerField(_("ratings received"), default=0)
    total_lessons_taught = models.PositiveIntegerField(_("lessons taught"), default=0)
    emergency_contact_name = models.CharField(
        _("emergency contact name"), max_length=150, blank=True
    )
    emergency_contact_phone = models.CharField(
        _("emergency contact phone"), max_length=25, blank=True, validators=[phone_validator]
    )

    class Meta:
        verbose_name = _("instructor")
        verbose_name_plural = _("instructors")
        ordering = ["user__first_name", "user__last_name", "instructor_code"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["is_active", "is_available_for_booking"]),
            models.Index(fields=["max_level_taught", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.instructor_code})" if self.instructor_code else self.full_name

    # -- identity ----------------------------------------------------------
    @property
    def full_name(self) -> str:
        if self.user_id is None:
            return str(self.instructor_code or _("Unnamed instructor"))
        return self.user.get_display_name()

    @property
    def initials(self) -> str:
        return self.user.initials if self.user_id else "?"

    @property
    def level_rank(self) -> int:
        """Ordinal of the highest level this instructor may teach."""
        return level_rank(self.max_level_taught)

    # -- certifications ----------------------------------------------------
    def current_certifications(self):
        """Verified certifications that have not expired."""
        today = timezone.localdate()
        return self.certifications.filter(is_verified=True).filter(
            models.Q(expires_on__isnull=True) | models.Q(expires_on__gte=today)
        )

    @property
    def has_valid_certifications(self) -> bool:
        """True when every mandatory credential group is current.

        The three groups are the coaching award, the water-safety / rescue award
        and first aid. A coaching award without a current rescue award is not
        valid for practice, so all three must be present.
        """
        held = set(self.current_certifications().values_list("kind", flat=True))
        return all(bool(held & set(group)) for group in Certification.REQUIRED_GROUPS)

    @property
    def missing_certification_groups(self) -> list:
        """Labels of the mandatory credential groups that are not current."""
        held = set(self.current_certifications().values_list("kind", flat=True))
        return [
            label
            for label, group in Certification.REQUIRED_GROUP_LABELS
            if not (held & set(group))
        ]

    @property
    def expiring_certifications(self):
        """Certifications expiring within :data:`EXPIRY_WARNING_DAYS` days."""
        today = timezone.localdate()
        return self.certifications.filter(
            expires_on__gte=today,
            expires_on__lte=today + dt.timedelta(days=EXPIRY_WARNING_DAYS),
        )

    @property
    def expired_certifications(self):
        return self.certifications.filter(expires_on__lt=timezone.localdate())

    @property
    def has_certification_warning(self) -> bool:
        return self.expiring_certifications.exists() or self.expired_certifications.exists()

    # -- validation & persistence -----------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.hire_date and self.hire_date > timezone.localdate():
            errors.setdefault("hire_date", []).append(_("The hire date cannot be in the future."))
        if isinstance(self.specialties, str):
            errors.setdefault("specialties", []).append(_("Specialties must be a list of values."))
        if isinstance(self.languages, str):
            errors.setdefault("languages", []).append(_("Languages must be a list of codes."))
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.instructor_code:
            self.instructor_code = next_sequential_code(
                Instructor, "instructor_code", "INS", width=4
            )
        if self.specialties is None:
            self.specialties = []
        if self.languages is None:
            self.languages = []
        super().save(*args, **kwargs)


class Certification(BaseModel):
    """One credential held by an instructor, with its own expiry."""

    class Kind(models.TextChoices):
        ISA_L1 = "isa_l1", _("ISA Surf Level 1 Instructor")
        ISA_L2 = "isa_l2", _("ISA Surf Level 2 Coach")
        SURFING_ENGLAND = "surfing_england", _("Surfing England coaching award")
        LIFEGUARD = "lifeguard", _("Surf lifeguard / water safety")
        FIRST_AID = "first_aid", _("First aid")
        CPR = "cpr", _("CPR")
        SAFEGUARDING = "safeguarding", _("Safeguarding")
        POWERBOAT = "powerboat", _("Powerboat / rescue craft")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        CURRENT = "current", _("Current")
        EXPIRING = "expiring", _("Expiring soon")
        EXPIRED = "expired", _("Expired")
        UNVERIFIED = "unverified", _("Awaiting verification")

    #: Awards that qualify somebody to coach at all.
    COACHING_KINDS = (Kind.ISA_L1, Kind.ISA_L2, Kind.SURFING_ENGLAND)
    #: Awards accepted for intermediate level and above (Level 2 or higher).
    SENIOR_COACHING_KINDS = (Kind.ISA_L2, Kind.SURFING_ENGLAND)
    #: Water-safety / rescue awards. Mandatory alongside any coaching award.
    RESCUE_KINDS = (Kind.LIFEGUARD, Kind.POWERBOAT)
    #: First-aid awards.
    FIRST_AID_KINDS = (Kind.FIRST_AID, Kind.CPR)

    #: Every group here must hold at least one current certification before an
    #: instructor counts as fully qualified.
    REQUIRED_GROUPS = (COACHING_KINDS, RESCUE_KINDS, FIRST_AID_KINDS)
    REQUIRED_GROUP_LABELS = (
        (_("Coaching award"), COACHING_KINDS),
        (_("Water safety / rescue"), RESCUE_KINDS),
        (_("First aid"), FIRST_AID_KINDS),
    )

    instructor = models.ForeignKey(
        Instructor,
        verbose_name=_("instructor"),
        on_delete=models.CASCADE,
        related_name="certifications",
    )
    kind = models.CharField(
        _("type"), max_length=20, choices=Kind.choices, default=Kind.OTHER, db_index=True
    )
    name = models.CharField(
        _("name"), max_length=200, help_text=_("Exact title printed on the certificate.")
    )
    issuing_body = models.CharField(_("issuing body"), max_length=150, blank=True)
    certificate_number = models.CharField(_("certificate number"), max_length=100, blank=True)
    issued_on = models.DateField(_("issued on"))
    expires_on = models.DateField(
        _("expires on"),
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Leave empty for a credential that does not expire."),
    )
    document = models.FileField(
        _("evidence"),
        upload_to="instructors/certifications/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_document_upload],
    )
    is_verified = models.BooleanField(
        _("verified"),
        default=False,
        db_index=True,
        help_text=_("A named staff member has seen the original certificate."),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("verified by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_certifications",
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)

    class Meta:
        verbose_name = _("certification")
        verbose_name_plural = _("certifications")
        ordering = [models.F("expires_on").asc(nulls_last=True), "kind"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["instructor", "kind"]),
            models.Index(fields=["expires_on", "is_verified"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["instructor", "kind", "certificate_number"],
                condition=models.Q(is_deleted=False) & ~models.Q(certificate_number=""),
                name="uniq_certificate_number_per_instructor_kind",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.name}"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < timezone.localdate())

    @property
    def days_until_expiry(self) -> int | None:
        if not self.expires_on:
            return None
        return (self.expires_on - timezone.localdate()).days

    @property
    def is_expiring_soon(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= EXPIRY_WARNING_DAYS

    @property
    def status(self) -> str:
        if self.is_expired:
            return self.Status.EXPIRED
        if not self.is_verified:
            return self.Status.UNVERIFIED
        if self.is_expiring_soon:
            return self.Status.EXPIRING
        return self.Status.CURRENT

    @property
    def status_label(self):
        return self.Status(self.status).label

    @property
    def is_current(self) -> bool:
        """Verified and not expired — the only state that permits teaching."""
        return self.is_verified and not self.is_expired

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.issued_on and self.issued_on > timezone.localdate():
            errors.setdefault("issued_on", []).append(
                _("The issue date cannot be in the future.")
            )
        if self.issued_on and self.expires_on and self.expires_on <= self.issued_on:
            errors.setdefault("expires_on", []).append(
                _("The expiry date must be after the issue date.")
            )
        if errors:
            raise ValidationError(errors)


class AvailabilitySlot(TimeStampedModel):
    """A recurring weekly window in which an instructor can be booked."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, _("Monday")
        TUESDAY = 1, _("Tuesday")
        WEDNESDAY = 2, _("Wednesday")
        THURSDAY = 3, _("Thursday")
        FRIDAY = 4, _("Friday")
        SATURDAY = 5, _("Saturday")
        SUNDAY = 6, _("Sunday")

    instructor = models.ForeignKey(
        Instructor,
        verbose_name=_("instructor"),
        on_delete=models.CASCADE,
        related_name="availability_slots",
    )
    weekday = models.PositiveSmallIntegerField(
        _("weekday"), choices=Weekday.choices, db_index=True
    )
    start_time = models.TimeField(_("from"))
    end_time = models.TimeField(_("to"))
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    valid_from = models.DateField(
        _("valid from"),
        null=True,
        blank=True,
        help_text=_("Leave empty for a pattern that has always applied."),
    )
    valid_until = models.DateField(
        _("valid until"),
        null=True,
        blank=True,
        help_text=_("Leave empty for a pattern with no end date."),
    )

    class Meta:
        verbose_name = _("availability slot")
        verbose_name_plural = _("availability slots")
        ordering = ["weekday", "start_time"]
        indexes = [models.Index(fields=["instructor", "weekday", "is_active"])]
        constraints = [
            models.UniqueConstraint(
                fields=["instructor", "weekday", "start_time"],
                name="uniq_availability_slot_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors.setdefault("end_time", []).append(
                _("The end time must be after the start time.")
            )
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            errors.setdefault("valid_until", []).append(
                _("The end of the validity window must not be before its start.")
            )
        if errors:
            raise ValidationError(errors)

    # -- helpers used by the availability service --------------------------
    def is_valid_on(self, on_date: dt.date) -> bool:
        if self.valid_from and on_date < self.valid_from:
            return False
        if self.valid_until and on_date > self.valid_until:
            return False
        return True

    def covers(self, start_time: dt.time, end_time: dt.time) -> bool:
        return self.start_time <= start_time and self.end_time >= end_time

    @property
    def duration_minutes(self) -> int:
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(0, end - start)


class TimeOff(BaseModel):
    """A period during which an instructor must not be scheduled."""

    class Reason(models.TextChoices):
        HOLIDAY = "holiday", _("Holiday")
        SICK = "sick", _("Sick leave")
        TRAINING = "training", _("Training / course")
        PERSONAL = "personal", _("Personal")
        OTHER = "other", _("Other")

    instructor = models.ForeignKey(
        Instructor,
        verbose_name=_("instructor"),
        on_delete=models.CASCADE,
        related_name="time_off_periods",
    )
    start_date = models.DateField(_("from"), db_index=True)
    end_date = models.DateField(_("to"), db_index=True)
    reason = models.CharField(
        _("reason"), max_length=20, choices=Reason.choices, default=Reason.HOLIDAY, db_index=True
    )
    note = models.TextField(_("note"), blank=True)
    is_approved = models.BooleanField(
        _("approved"),
        default=False,
        db_index=True,
        help_text=_("Only approved absence removes an instructor from the booking pool."),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("approved by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_time_off_periods",
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)

    class Meta:
        verbose_name = _("time off")
        verbose_name_plural = _("time off")
        ordering = ["-start_date"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["instructor", "start_date", "end_date"]),
            models.Index(fields=["is_approved", "start_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.instructor_id}: {self.start_date} → {self.end_date}"

    @property
    def total_days(self) -> int:
        if not (self.start_date and self.end_date):
            return 0
        return (self.end_date - self.start_date).days + 1

    @property
    def is_current(self) -> bool:
        today = timezone.localdate()
        return self.start_date <= today <= self.end_date

    @property
    def is_past(self) -> bool:
        return bool(self.end_date and self.end_date < timezone.localdate())

    def covers(self, on_date: dt.date) -> bool:
        return self.start_date <= on_date <= self.end_date

    def clean(self) -> None:
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": [_("The end date must not be before the start date.")]}
            )


class PerformanceReview(BaseModel):
    """A periodic appraisal of an instructor by a named reviewer."""

    class Score(models.IntegerChoices):
        ONE = 1, _("1 — Needs immediate attention")
        TWO = 2, _("2 — Below expectations")
        THREE = 3, _("3 — Meets expectations")
        FOUR = 4, _("4 — Above expectations")
        FIVE = 5, _("5 — Outstanding")

    SCORE_FIELDS = (
        "teaching_quality",
        "punctuality",
        "safety",
        "communication",
        "teamwork",
    )

    instructor = models.ForeignKey(
        Instructor,
        verbose_name=_("instructor"),
        on_delete=models.CASCADE,
        related_name="performance_reviews",
    )
    period_start = models.DateField(_("period from"))
    period_end = models.DateField(_("period to"))
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reviewer"),
        on_delete=models.PROTECT,
        related_name="instructor_reviews_written",
    )
    teaching_quality = models.PositiveSmallIntegerField(
        _("teaching quality"), choices=Score.choices, default=Score.THREE
    )
    punctuality = models.PositiveSmallIntegerField(
        _("punctuality"), choices=Score.choices, default=Score.THREE
    )
    safety = models.PositiveSmallIntegerField(
        _("safety awareness"), choices=Score.choices, default=Score.THREE
    )
    communication = models.PositiveSmallIntegerField(
        _("communication"), choices=Score.choices, default=Score.THREE
    )
    teamwork = models.PositiveSmallIntegerField(
        _("teamwork"), choices=Score.choices, default=Score.THREE
    )
    strengths = models.TextField(_("strengths"), blank=True)
    improvements = models.TextField(_("areas to improve"), blank=True)
    goals = models.TextField(_("goals for next period"), blank=True)
    overall_score = models.DecimalField(
        _("overall score"),
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )

    class Meta:
        verbose_name = _("performance review")
        verbose_name_plural = _("performance reviews")
        ordering = ["-period_end", "-created_at"]
        base_manager_name = "all_objects"
        indexes = [models.Index(fields=["instructor", "period_end"])]

    def __str__(self) -> str:
        return f"{self.instructor_id} {self.period_start}–{self.period_end}"

    def compute_overall_score(self) -> Decimal:
        values = [int(getattr(self, field) or 0) for field in self.SCORE_FIELDS]
        if not values:
            return Decimal("0.00")
        total = Decimal(sum(values))
        return (total / Decimal(len(values))).quantize(Decimal("0.01"))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.period_start and self.period_end and self.period_end < self.period_start:
            errors.setdefault("period_end", []).append(
                _("The end of the period must not be before its start.")
            )
        for field in self.SCORE_FIELDS:
            value = getattr(self, field, None)
            if value is not None and not (1 <= int(value) <= 5):
                errors.setdefault(field, []).append(_("Score must be between 1 and 5."))
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.overall_score = self.compute_overall_score()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(set(kwargs["update_fields"]) | {"overall_score"})
        super().save(*args, **kwargs)
