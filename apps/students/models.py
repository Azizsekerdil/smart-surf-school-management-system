"""Student and skill-assessment models.

The safety rules encoded here come from ``docs/research/SURF_DOMAIN_MODEL.md``:
level-appropriate lessons, swimming competence, and the stricter treatment of
under-18s. :meth:`Student.can_join_lesson` is the single gate the booking and
lesson modules call before putting somebody in a group.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel
from apps.core.enums import level_rank as level_rank_of
from apps.core.enums import recommended_board_volume as calculate_board_volume
from apps.core.models import BaseModel
from apps.core.utils import next_sequential_code

STUDENT_CODE_PREFIX = "STU"
STUDENT_CODE_WIDTH = 5

#: Levels a non-swimmer may still be taught: whitewater, standing depth, with an
#: instructor within arm's reach. Anything beyond this needs real water
#: competence (see §3.2 of the domain research).
NON_SWIMMER_MAX_LEVEL = SurfLevel.BEGINNER

#: Distance (metres) a student should be able to swim unassisted before being
#: taught outside standing depth.
MIN_SWIM_DISTANCE_M = 25

#: Scores are a 1–5 coaching scale, not a percentage.
SCORE_MIN = 1
SCORE_MAX = 5
SCORE_VALIDATORS = [MinValueValidator(SCORE_MIN), MaxValueValidator(SCORE_MAX)]

#: The five competencies assessed at every level, in coaching order.
SKILL_FIELDS: tuple[str, ...] = (
    "paddling",
    "popup",
    "positioning",
    "wave_reading",
    "safety",
)


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def at_level(self, level: str):
        return self.filter(surf_level=level)

    def for_instructor(self, instructor_id):
        return self.filter(preferred_instructor_id=instructor_id)

    def search(self, term: str):
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            Q(student_code__icontains=term)
            | Q(customer__first_name__icontains=term)
            | Q(customer__last_name__icontains=term)
            | Q(customer__customer_code__icontains=term)
            | Q(customer__email__icontains=term)
        )


class StudentManager(models.Manager.from_queryset(StudentQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class StudentAllObjectsManager(models.Manager.from_queryset(StudentQuerySet)):
    pass


class Student(BaseModel):
    """A person we teach. One per customer."""

    class Stance(models.TextChoices):
        REGULAR = "regular", _("Regular (left foot forward)")
        GOOFY = "goofy", _("Goofy (right foot forward)")
        UNKNOWN = "unknown", _("Not yet known")

    class BoardPreference(models.TextChoices):
        SOFTTOP = "softtop", _("Soft-top / foamie")
        LONGBOARD = "longboard", _("Longboard")
        FUNBOARD = "funboard", _("Funboard / mini-malibu")
        FISH = "fish", _("Fish")
        SHORTBOARD = "shortboard", _("Shortboard")
        __empty__ = _("No preference")

    customer = models.OneToOneField(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="student_profile",
    )
    student_code = models.CharField(
        _("student code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Generated automatically, e.g. STU00001."),
    )

    # --- surfing ----------------------------------------------------------
    surf_level = models.CharField(
        _("surf level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.FIRST_TIME,
        db_index=True,
    )
    goals = models.TextField(
        _("goals"), blank=True, help_text=_("What does this student want to achieve?")
    )
    stance = models.CharField(
        _("stance"), max_length=10, choices=Stance.choices, default=Stance.UNKNOWN
    )
    board_preference = models.CharField(
        _("board preference"),
        max_length=20,
        choices=BoardPreference.choices,
        blank=True,
        default="",
    )

    # --- water competence -------------------------------------------------
    can_swim = models.BooleanField(
        _("can swim"),
        default=False,
        db_index=True,
        help_text=_("Confirmed by the student or their guardian, not assumed."),
    )
    swim_distance_m = models.PositiveSmallIntegerField(
        _("swim distance (m)"),
        null=True,
        blank=True,
        help_text=_("Unassisted, in open water. 25 m is the usual school minimum."),
    )

    # --- medical ----------------------------------------------------------
    medical_conditions = models.TextField(
        _("medical conditions"),
        blank=True,
        help_text=_("Asthma, epilepsy, heart conditions, recent surgery, …"),
    )
    medications = models.TextField(_("medications"), blank=True)
    allergies = models.TextField(_("allergies"), blank=True)

    # --- sizing -----------------------------------------------------------
    weight_kg = models.DecimalField(
        _("weight (kg)"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("10.00")), MaxValueValidator(Decimal("250.00"))],
    )
    height_cm = models.PositiveSmallIntegerField(
        _("height (cm)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(60), MaxValueValidator(250)],
    )
    shoe_size = models.PositiveSmallIntegerField(
        _("shoe size (EU)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(20), MaxValueValidator(52)],
        help_text=_("Used for booties."),
    )
    wetsuit_size = models.CharField(
        _("wetsuit size"),
        max_length=10,
        blank=True,
        help_text=_("As labelled on the school's suits, e.g. MS, L, JR12."),
    )

    # --- relationships ----------------------------------------------------
    preferred_instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("preferred instructor"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferring_students",
    )

    # --- roll-ups ---------------------------------------------------------
    total_lessons = models.PositiveIntegerField(_("lessons taken"), default=0)
    total_hours = models.DecimalField(
        _("hours in the water"), max_digits=7, decimal_places=2, default=Decimal("0.00")
    )
    last_lesson_date = models.DateField(_("last lesson"), null=True, blank=True, db_index=True)

    joined_at = models.DateField(_("joined on"), default=timezone.localdate)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    objects = StudentManager()
    all_objects = StudentAllObjectsManager()

    class Meta:
        verbose_name = _("student")
        verbose_name_plural = _("students")
        ordering = ["customer__last_name", "customer__first_name"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["surf_level", "is_active"]),
            models.Index(fields=["-last_lesson_date"]),
            models.Index(fields=["preferred_instructor", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.student_code})" if self.student_code else self.full_name

    # ------------------------------------------------------------------ data
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.swim_distance_m and not self.can_swim:
            errors.setdefault("can_swim", []).append(
                ValidationError(
                    _("A swim distance was entered — tick “can swim” to confirm it.")
                )
            )
        if self.joined_at and self.joined_at > timezone.localdate():
            errors.setdefault("joined_at", []).append(
                ValidationError(_("The join date cannot be in the future."))
            )
        if self.last_lesson_date and self.joined_at and self.last_lesson_date < self.joined_at:
            errors.setdefault("last_lesson_date", []).append(
                ValidationError(_("The last lesson cannot predate the join date."))
            )
        # A student above beginner level who cannot swim is a data error, and a
        # dangerous one: it would let the booking screen put them beyond depth.
        if not self.can_swim and level_rank_of(self.surf_level) > level_rank_of(NON_SWIMMER_MAX_LEVEL):
            errors.setdefault("surf_level", []).append(
                ValidationError(
                    _(
                        "A student who cannot swim may not be recorded above "
                        "“%(level)s”. Confirm swimming ability first."
                    )
                    % {"level": SurfLevel(NON_SWIMMER_MAX_LEVEL).label}
                )
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.student_code:
            self.student_code = next_sequential_code(
                Student, "student_code", STUDENT_CODE_PREFIX, STUDENT_CODE_WIDTH
            )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = sorted(set(update_fields) | {"student_code"})
        super().save(*args, **kwargs)

    # ------------------------------------------------------------ properties
    @property
    def full_name(self) -> str:
        return self.customer.full_name if self.customer_id else ""

    @property
    def age(self) -> int | None:
        return self.customer.age if self.customer_id else None

    @property
    def is_minor(self) -> bool:
        return bool(self.customer_id and self.customer.is_minor)

    @property
    def level_rank(self) -> int:
        return level_rank_of(self.surf_level)

    @property
    def recommended_board_volume(self) -> float | None:
        """Litres of board volume this rider should be on, or ``None``.

        ``volume ≈ weight_kg × coefficient(level)`` — a beginner floats on
        roughly 1.0 L/kg, a competition surfer on about 0.36 L/kg.
        """
        return calculate_board_volume(self.weight_kg, self.surf_level)

    @property
    def recommended_board_type(self) -> str:
        """The board class that matches the level, independent of preference."""
        rank = self.level_rank
        if rank <= level_rank_of(SurfLevel.BEGINNER):
            return str(_("Soft-top, 7–9 ft"))
        if rank == level_rank_of(SurfLevel.ADVANCED_BEGINNER):
            return str(_("Soft-top or funboard, 7–8 ft"))
        if rank == level_rank_of(SurfLevel.INTERMEDIATE):
            return str(_("Funboard or fish, 6'4\"–7'2\""))
        return str(_("Performance shortboard"))

    @property
    def has_medical_flags(self) -> bool:
        return bool(
            (self.medical_conditions or "").strip()
            or (self.medications or "").strip()
            or (self.allergies or "").strip()
        )

    @property
    def swim_distance_is_sufficient(self) -> bool:
        return bool(self.can_swim and (self.swim_distance_m or 0) >= MIN_SWIM_DISTANCE_M)

    @property
    def latest_assessment(self):
        return self.assessments.order_by("-assessed_on", "-created_at").first()

    # --------------------------------------------------------------- methods
    def can_join_lesson(self, lesson) -> tuple[bool, str]:
        """Decide whether this student may join *lesson*.

        Returns ``(allowed, reason)``; *reason* is empty when allowed and is a
        human-readable, translated explanation otherwise. The first blocking
        reason wins, ordered by severity: safety restrictions, then water
        competence, then waiver, then level fit, then age.

        The safety module is looked up lazily via the app registry, so this
        method works in a deployment where ``apps.safety`` holds no restriction
        rows — or is not installed at all.
        """
        if not self.is_active:
            return False, str(_("This student profile is archived."))
        if not self.customer.is_active:
            return False, str(_("The linked customer record is archived."))

        # 1. Explicit safety restrictions always win.
        blocked, reason = self._safety_restriction_reason(lesson)
        if blocked:
            return False, reason

        target_level = _lesson_level(lesson)
        min_level = _lesson_attr(lesson, "min_level")
        max_level = _lesson_attr(lesson, "max_level")

        # 2. Water competence.
        if not self.can_swim:
            ceiling = target_level or min_level
            if ceiling and level_rank_of(ceiling) > level_rank_of(NON_SWIMMER_MAX_LEVEL):
                return False, str(
                    _("This student cannot swim and may only join whitewater lessons.")
                )
            if _lesson_flag(lesson, "requires_swim_ability"):
                return False, str(_("This lesson requires confirmed swimming ability."))

        # 3. Waiver — nobody enters the water without one.
        if not self.customer.has_valid_waiver():
            return False, str(_("No signed, unexpired waiver is on file."))

        # 4. Level fit.
        if min_level and self.level_rank < level_rank_of(min_level):
            return False, str(
                _("This lesson starts at %(level)s; the student is at %(current)s.")
                % {
                    "level": SurfLevel(min_level).label,
                    "current": self.get_surf_level_display(),
                }
            )
        if max_level and self.level_rank > level_rank_of(max_level):
            return False, str(
                _("This lesson tops out at %(level)s; the student is beyond it.")
                % {"level": SurfLevel(max_level).label}
            )

        # 5. Age and guardian cover.
        age = self.age
        min_age = _lesson_number(lesson, "min_age")
        max_age = _lesson_number(lesson, "max_age")
        if min_age is not None and age is not None and age < min_age:
            return False, str(
                _("This lesson is for ages %(min)s and over.") % {"min": min_age}
            )
        if max_age is not None and age is not None and age > max_age:
            return False, str(_("This lesson is for ages %(max)s and under.") % {"max": max_age})
        if self.is_minor and not self.customer.has_emergency_contact:
            return False, str(
                _("A guardian name and phone number are required for a student under 18.")
            )

        return True, ""

    def _safety_restriction_reason(self, lesson) -> tuple[bool, str]:
        """Consult ``safety.StudentRestriction`` without importing the module."""
        try:
            Restriction = django_apps.get_model("safety", "StudentRestriction")
        except (LookupError, ValueError):
            return False, ""
        if Restriction is None:
            return False, ""

        field_names = {field.name for field in Restriction._meta.get_fields()}
        if "student" not in field_names:
            return False, ""

        manager = getattr(Restriction, "objects", None) or Restriction._default_manager
        queryset = manager.filter(student=self)
        if "is_active" in field_names:
            queryset = queryset.filter(is_active=True)
        if "is_lifted" in field_names:
            queryset = queryset.filter(is_lifted=False)

        today = timezone.localdate()
        for field in ("expires_on", "valid_until", "until"):
            if field in field_names:
                queryset = queryset.filter(
                    Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__gte": today})
                )
                break

        restriction = queryset.first()
        if restriction is None:
            return False, ""

        detail = ""
        for field in ("reason", "description", "note"):
            value = getattr(restriction, field, "")
            if value:
                detail = str(value)
                break
        return True, str(
            _("A safety restriction is in force for this student. %(detail)s")
            % {"detail": detail}
        ).strip()


# ---------------------------------------------------------------------------
# Lesson introspection helpers
# ---------------------------------------------------------------------------
# The lessons module owns the Lesson/LessonType shape. Reading it defensively
# keeps this gate working whether the level bounds sit on the lesson itself or
# on its lesson type.
def _lesson_attr(lesson, name: str):
    if lesson is None:
        return None
    value = getattr(lesson, name, None)
    if value:
        return value
    lesson_type = getattr(lesson, "lesson_type", None)
    return getattr(lesson_type, name, None) if lesson_type is not None else None


def _lesson_level(lesson):
    return _lesson_attr(lesson, "level") or _lesson_attr(lesson, "surf_level")


def _lesson_flag(lesson, name: str) -> bool:
    return bool(_lesson_attr(lesson, name))


def _lesson_number(lesson, name: str):
    value = _lesson_attr(lesson, name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class SkillAssessment(BaseModel):
    """One coach's evaluation of a student on a given day.

    Assessments are the audit trail behind a level change: a student moves up
    because a named instructor scored them, on a date, with notes.
    """

    student = models.ForeignKey(
        Student,
        verbose_name=_("student"),
        on_delete=models.PROTECT,
        related_name="assessments",
    )
    instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("instructor"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="skill_assessments",
    )
    assessed_on = models.DateField(_("assessed on"), default=timezone.localdate, db_index=True)

    level_before = models.CharField(
        _("level before"), max_length=20, choices=SurfLevel.choices
    )
    level_after = models.CharField(_("level after"), max_length=20, choices=SurfLevel.choices)

    paddling = models.PositiveSmallIntegerField(
        _("paddling"), validators=SCORE_VALIDATORS, help_text=_("1 = struggling, 5 = strong")
    )
    popup = models.PositiveSmallIntegerField(_("pop-up"), validators=SCORE_VALIDATORS)
    positioning = models.PositiveSmallIntegerField(
        _("positioning"), validators=SCORE_VALIDATORS
    )
    wave_reading = models.PositiveSmallIntegerField(
        _("wave reading"), validators=SCORE_VALIDATORS
    )
    safety = models.PositiveSmallIntegerField(
        _("safety awareness"),
        validators=SCORE_VALIDATORS,
        help_text=_("Rips, other surfers, board control, self-rescue."),
    )

    notes = models.TextField(_("notes"), blank=True)
    next_focus = models.CharField(
        _("next focus"),
        max_length=200,
        blank=True,
        help_text=_("The one thing to work on in the next session."),
    )

    class Meta:
        verbose_name = _("skill assessment")
        verbose_name_plural = _("skill assessments")
        ordering = ["-assessed_on", "-created_at"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["student", "-assessed_on"]),
            models.Index(fields=["instructor", "-assessed_on"]),
        ]

    def __str__(self) -> str:
        return f"{self.student} — {self.assessed_on}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.assessed_on and self.assessed_on > timezone.localdate():
            errors.setdefault("assessed_on", []).append(
                ValidationError(_("An assessment cannot be dated in the future."))
            )
        # Jumping more than one level at a time is almost always a typo, and a
        # student promoted too far ends up in surf they cannot handle.
        if self.level_before and self.level_after:
            jump = level_rank_of(self.level_after) - level_rank_of(self.level_before)
            if jump > 1:
                errors.setdefault("level_after", []).append(
                    ValidationError(
                        _("A student may only move up one level per assessment.")
                    )
                )
        if errors:
            raise ValidationError(errors)

    @property
    def average_score(self) -> float:
        """Mean of the five competency scores, to one decimal place."""
        values = [getattr(self, name) or 0 for name in SKILL_FIELDS]
        if not values:
            return 0.0
        return round(sum(values) / len(values), 1)

    @property
    def scores(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in SKILL_FIELDS}

    @property
    def level_changed(self) -> bool:
        return self.level_before != self.level_after

    @property
    def weakest_skill(self) -> str:
        """Field name of the lowest score — what the next lesson should target."""
        return min(SKILL_FIELDS, key=lambda name: getattr(self, name) or 0)
