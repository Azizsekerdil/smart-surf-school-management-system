"""Lesson catalogue, scheduled lessons and the attendance roster.

Three models, three responsibilities
------------------------------------
``LessonType``       the sellable product: level band, age band, duration,
                     group size, price and the kit it requires.
``Lesson``           one dated, timed, staffed instance of a type at a spot.
``LessonAttendance`` one student's seat on one lesson, from booking through
                     check-in to attended / no-show, plus the board and wetsuit
                     they were handed.

Safety note
-----------
Group size is not a preference. ``MAX_STUDENTS_PER_INSTRUCTOR`` (and the
stricter ``MAX_STUDENTS_PER_INSTRUCTOR_MINORS``) come from the governing-body
ratios recorded in ``docs/research/SURF_DOMAIN_MODEL.md`` and are enforced in
:meth:`Lesson.clean` as well as in the services layer, so neither the admin nor
the API can create an unsafe group.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    LessonStatus,
    SurfLevel,
    level_rank,
)
from apps.core.models import BaseModel, money_field

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------
#: Lesson statuses that still occupy an instructor, a spot and a time slot.
LIVE_LESSON_STATUSES: tuple[str, ...] = (
    LessonStatus.SCHEDULED,
    LessonStatus.CONFIRMED,
    LessonStatus.IN_PROGRESS,
)

#: Lesson statuses after which the roster must no longer change.
CLOSED_LESSON_STATUSES: tuple[str, ...] = (
    LessonStatus.COMPLETED,
    LessonStatus.CANCELLED,
)

hex_colour_validator = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    message=_("Enter a hexadecimal colour such as #0ea5e9."),
)


def ratio_limit(level: str, instructor_count: int = 1, has_minors: bool = False) -> int:
    """Maximum students allowed in the water for *level*.

    ``instructor_count`` counts the lead instructor plus every assistant. The
    minors ceiling is applied per instructor, not to the whole group, because a
    second qualified adult genuinely doubles the supervision capacity — it does
    not licence a looser ratio per adult.
    """
    per_instructor = MAX_STUDENTS_PER_INSTRUCTOR.get(level, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)
    if has_minors:
        per_instructor = min(per_instructor, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)
    return max(1, per_instructor) * max(1, int(instructor_count or 1))


def student_is_minor(student, on_date: date_cls | None = None) -> bool:
    """Best-effort "is this student under 18 on *on_date*".

    The students app owns the birth date; this helper reads whichever of the
    conventional attributes exists so lessons never hard-depends on one of them.
    """
    if student is None:
        return False
    reference = on_date or timezone.localdate()

    explicit = getattr(student, "is_minor", None)
    if isinstance(explicit, bool):
        return explicit

    born = getattr(student, "date_of_birth", None) or getattr(student, "birth_date", None)
    if born:
        years = (
            reference.year
            - born.year
            - ((reference.month, reference.day) < (born.month, born.day))
        )
        return years < 18

    age = getattr(student, "age", None)
    if isinstance(age, int):
        return age < 18
    return False


def student_level(student) -> str:
    """Return the student's surf level, defaulting to first-timer."""
    value = getattr(student, "level", None) or getattr(student, "surf_level", None)
    return value or SurfLevel.FIRST_TIME


# ---------------------------------------------------------------------------
# Lesson catalogue
# ---------------------------------------------------------------------------
class LessonType(BaseModel):
    """A sellable teaching product.

    The level band drives the safety ratio; the age band drives the stricter
    minors ratio and the "this student is too young" gate at booking time.
    """

    class Category(models.TextChoices):
        FIRST_SURF = "first_surf", _("First surf experience")
        BEGINNER = "beginner", _("Beginner lesson")
        INTERMEDIATE = "intermediate", _("Intermediate lesson")
        ADVANCED_COACHING = "advanced_coaching", _("Advanced coaching")
        PRIVATE = "private", _("Private lesson")
        GROUP = "group", _("Group lesson")
        KIDS = "kids", _("Kids lesson")
        COMPETITION = "competition", _("Competition training")
        VIDEO_ANALYSIS = "video_analysis", _("Video analysis")
        CAMP_TRAINING = "camp_training", _("Surf camp training")

    code = models.CharField(_("code"), max_length=30, unique=True)
    name = models.CharField(_("name"), max_length=120)
    description = models.TextField(_("description"), blank=True)
    category = models.CharField(
        _("category"),
        max_length=20,
        choices=Category.choices,
        default=Category.GROUP,
        db_index=True,
    )

    min_level = models.CharField(
        _("minimum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.FIRST_TIME,
    )
    max_level = models.CharField(
        _("maximum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.BEGINNER,
        help_text=_("The hardest level taught. It also sets the safety ratio."),
    )
    min_age = models.PositiveSmallIntegerField(
        _("minimum age"), null=True, blank=True, help_text=_("Leave empty for no lower limit.")
    )
    max_age = models.PositiveSmallIntegerField(
        _("maximum age"), null=True, blank=True, help_text=_("Leave empty for no upper limit.")
    )

    duration_minutes = models.PositiveSmallIntegerField(
        _("duration (minutes)"), default=120, validators=[MinValueValidator(15)]
    )
    max_students = models.PositiveSmallIntegerField(
        _("maximum students"), default=8, validators=[MinValueValidator(1)]
    )
    min_students = models.PositiveSmallIntegerField(
        _("minimum students"), default=1, validators=[MinValueValidator(1)]
    )

    base_price = money_field(_("base price"))
    price_per_extra_student = money_field(
        _("price per extra student"),
        help_text=_("Charged for every participant above the minimum group size."),
    )

    requires_board = models.BooleanField(_("requires a board"), default=True)
    requires_wetsuit = models.BooleanField(_("requires a wetsuit"), default=True)
    requires_leash = models.BooleanField(_("requires a leash"), default=True)

    colour = models.CharField(
        _("calendar colour"),
        max_length=7,
        default="#0ea5e9",
        validators=[hex_colour_validator],
        help_text=_("Hexadecimal colour used on the lesson calendar."),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=100)

    class Meta:
        verbose_name = _("lesson type")
        verbose_name_plural = _("lesson types")
        ordering = ["sort_order", "name"]
        indexes = [models.Index(fields=["is_active", "sort_order"])]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    # -- derived ---------------------------------------------------------
    @property
    def is_private(self) -> bool:
        """A one-to-one product: private category, or a single-seat group."""
        return self.category == self.Category.PRIVATE or self.max_students <= 1

    @property
    def allowed_levels(self) -> list[str]:
        """Every surf level this product may be taught to, low to high."""
        low, high = level_rank(self.min_level), level_rank(self.max_level)
        if high < low:
            low, high = high, low
        return [value for value, _label in SurfLevel.choices if low <= level_rank(value) <= high]

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_minutes)

    @property
    def ratio_per_instructor(self) -> int:
        """Students one instructor may supervise at this product's top level."""
        return ratio_limit(self.max_level, instructor_count=1)

    def accepts_level(self, level: str) -> bool:
        return level in self.allowed_levels

    def accepts_age(self, age: int | None) -> bool:
        if age is None:
            return True
        if self.min_age is not None and age < self.min_age:
            return False
        if self.max_age is not None and age > self.max_age:
            return False
        return True

    def price_for(self, student_count: int, base: Decimal | None = None) -> Decimal:
        """Price of one session with *student_count* participants.

        ``base_price`` covers the minimum group size; every participant beyond
        it adds ``price_per_extra_student``.
        """
        base_amount = self.base_price if base is None else base
        extra = max(0, int(student_count or 0) - int(self.min_students or 1))
        return Decimal(base_amount) + (Decimal(self.price_per_extra_student) * extra)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.min_students and self.max_students and self.min_students > self.max_students:
            errors.setdefault("min_students", []).append(
                _("The minimum group size cannot exceed the maximum group size.")
            )
        if level_rank(self.max_level) < level_rank(self.min_level):
            errors.setdefault("max_level", []).append(
                _("The maximum level cannot be lower than the minimum level.")
            )
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            errors.setdefault("max_age", []).append(
                _("The maximum age cannot be lower than the minimum age.")
            )
        safe_ceiling = ratio_limit(self.max_level, instructor_count=1)
        if self.max_students and self.max_students > safe_ceiling * 4:
            errors.setdefault("max_students", []).append(
                _(
                    "A group of %(size)s would need more than four instructors at this "
                    "level (safety ratio is %(ratio)s students per instructor)."
                )
                % {"size": self.max_students, "ratio": safe_ceiling}
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Scheduled lesson
# ---------------------------------------------------------------------------
class Lesson(BaseModel):
    """One dated, staffed teaching session at a surf spot."""

    lesson_code = models.CharField(_("lesson code"), max_length=20, unique=True, blank=True)

    lesson_type = models.ForeignKey(
        LessonType,
        verbose_name=_("lesson type"),
        on_delete=models.PROTECT,
        related_name="lessons",
    )
    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.PROTECT,
        related_name="lessons",
    )

    date = models.DateField(_("date"), db_index=True)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))

    instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("instructor"),
        on_delete=models.PROTECT,
        related_name="lessons",
    )
    assistant_instructors = models.ManyToManyField(
        "instructors.Instructor",
        verbose_name=_("assistant instructors"),
        blank=True,
        related_name="assisted_lessons",
    )

    capacity = models.PositiveSmallIntegerField(
        _("capacity"), default=8, validators=[MinValueValidator(1)]
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=LessonStatus.choices,
        default=LessonStatus.SCHEDULED,
        db_index=True,
    )
    price_override = money_field(
        _("price override"),
        default=None,
        null=True,
        blank=True,
        help_text=_("Leave empty to use the lesson type's base price."),
    )

    notes = models.TextField(_("notes"), blank=True, help_text=_("Visible to customers."))
    internal_notes = models.TextField(
        _("internal notes"), blank=True, help_text=_("Never shown to customers.")
    )

    conditions_snapshot = models.JSONField(
        _("surf conditions snapshot"),
        default=dict,
        blank=True,
        help_text=_("Conditions frozen at lesson time. Legal evidence — never recomputed."),
    )

    safety_briefing_done = models.BooleanField(_("safety briefing delivered"), default=False)
    safety_checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("safety checked by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="safety_checked_lessons",
    )
    safety_checked_at = models.DateTimeField(_("safety checked at"), null=True, blank=True)

    cancellation_reason = models.TextField(_("cancellation reason"), blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    class Meta:
        verbose_name = _("lesson")
        verbose_name_plural = _("lessons")
        ordering = ["-date", "start_time"]
        indexes = [
            models.Index(fields=["date", "status"]),
            models.Index(fields=["instructor", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.lesson_code} · {self.date:%d.%m.%Y} {self.start_time:%H:%M}"

    def save(self, *args, **kwargs):
        if not self.lesson_code:
            from apps.core.utils import next_sequential_code

            self.lesson_code = next_sequential_code(Lesson, "lesson_code", "LSN", width=5)
        super().save(*args, **kwargs)

    # -- time ------------------------------------------------------------
    @property
    def duration_minutes(self) -> int:
        start = datetime.combine(date_cls.min, self.start_time)
        end = datetime.combine(date_cls.min, self.end_time)
        return max(0, int((end - start).total_seconds() // 60))

    @property
    def starts_at(self) -> datetime:
        return self._aware(self.start_time)

    @property
    def ends_at(self) -> datetime:
        return self._aware(self.end_time)

    def _aware(self, at: time) -> datetime:
        moment = datetime.combine(self.date, at)
        if timezone.is_naive(moment):
            moment = timezone.make_aware(moment, timezone.get_current_timezone())
        return moment

    @property
    def is_past(self) -> bool:
        now = timezone.localtime()
        if self.date < now.date():
            return True
        return self.date == now.date() and self.end_time <= now.time()

    @property
    def is_editable(self) -> bool:
        return self.status not in CLOSED_LESSON_STATUSES

    # -- roster ----------------------------------------------------------
    def active_attendances(self):
        """Attendances that still hold a seat (registered / checked in / attended).

        The related manager is the soft-delete-agnostic ``all_objects`` manager,
        so deleted rows are excluded explicitly.
        """
        return self.attendances.filter(
            is_deleted=False, status__in=LessonAttendance.SEAT_TAKING_STATUSES
        )

    @property
    def booked_count(self) -> int:
        cached = getattr(self, "booked", None)
        if cached is not None:
            return int(cached)
        return self.active_attendances().count()

    @property
    def available_seats(self) -> int:
        return max(0, int(self.capacity) - self.booked_count)

    @property
    def is_full(self) -> bool:
        return self.booked_count >= int(self.capacity)

    @property
    def instructor_count(self) -> int:
        """Lead instructor plus assistants (assistants only count once saved)."""
        if not self.pk:
            return 1
        return 1 + self.assistant_instructors.count()

    @property
    def has_minors(self) -> bool:
        return any(
            student_is_minor(attendance.student, self.date)
            for attendance in self.active_attendances().select_related("student")
        )

    @property
    def max_students_allowed(self) -> int:
        """The safety ceiling for this lesson, given its staffing and roster."""
        return ratio_limit(
            self.lesson_type.max_level if self.lesson_type_id else SurfLevel.FIRST_TIME,
            instructor_count=self.instructor_count,
            has_minors=self.has_minors,
        )

    @property
    def required_ratio_ok(self) -> bool:
        """``True`` when the booked group is within the safety ratio."""
        return self.booked_count <= self.max_students_allowed

    # -- money -----------------------------------------------------------
    @property
    def price(self) -> Decimal:
        """Price of a seat's base session (override wins over the catalogue)."""
        if self.price_override is not None:
            return Decimal(self.price_override)
        if self.lesson_type_id:
            return Decimal(self.lesson_type.base_price)
        return Decimal("0.00")

    @property
    def total_price(self) -> Decimal:
        """Price of the whole session for the currently booked group."""
        if not self.lesson_type_id:
            return self.price
        return self.lesson_type.price_for(self.booked_count, base=self.price)

    # -- display ---------------------------------------------------------
    @property
    def colour(self) -> str:
        return self.lesson_type.colour if self.lesson_type_id else "#0ea5e9"

    @property
    def time_label(self) -> str:
        return f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"

    # -- validation ------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors.setdefault("end_time", []).append(
                _("The end time must be later than the start time.")
            )

        if self.lesson_type_id and self.capacity:
            if self.capacity > self.lesson_type.max_students:
                errors.setdefault("capacity", []).append(
                    _("%(type)s allows at most %(max)s students.")
                    % {"type": self.lesson_type.name, "max": self.lesson_type.max_students}
                )
            instructors = self.instructor_count
            ceiling = ratio_limit(
                self.lesson_type.max_level,
                instructor_count=instructors,
                has_minors=self.has_minors if self.pk else False,
            )
            if self.capacity > ceiling:
                errors.setdefault("capacity", []).append(
                    _(
                        "Safety ratio exceeded: %(instructors)s instructor(s) may supervise "
                        "at most %(max)s students at %(level)s level."
                    )
                    % {
                        "instructors": instructors,
                        "max": ceiling,
                        "level": self.lesson_type.get_max_level_display(),
                    }
                )

        if self.status == LessonStatus.CANCELLED and not self.cancellation_reason:
            errors.setdefault("cancellation_reason", []).append(
                _("A cancelled lesson must record why it was cancelled.")
            )

        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
class LessonAttendance(BaseModel):
    """One student's seat on one lesson."""

    class Status(models.TextChoices):
        REGISTERED = "registered", _("Registered")
        CHECKED_IN = "checked_in", _("Checked in")
        ATTENDED = "attended", _("Attended")
        NO_SHOW = "no_show", _("No show")
        CANCELLED = "cancelled", _("Cancelled")

    #: Statuses that consume a seat on the lesson.
    SEAT_TAKING_STATUSES: tuple[str, ...] = (
        Status.REGISTERED,
        Status.CHECKED_IN,
        Status.ATTENDED,
    )

    lesson = models.ForeignKey(
        Lesson,
        verbose_name=_("lesson"),
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.PROTECT,
        related_name="lesson_attendances",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendances",
    )

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.REGISTERED,
        db_index=True,
    )
    checked_in_at = models.DateTimeField(_("checked in at"), null=True, blank=True)

    rating = models.PositiveSmallIntegerField(
        _("student rating"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("How the student rated the lesson, 1 to 5."),
    )
    student_feedback = models.TextField(_("student feedback"), blank=True)
    instructor_notes = models.TextField(_("instructor notes"), blank=True)

    assigned_board = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("assigned board"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lesson_assignments",
    )
    assigned_wetsuit = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("assigned wetsuit"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wetsuit_assignments",
    )

    class Meta:
        verbose_name = _("lesson attendance")
        verbose_name_plural = _("lesson attendances")
        ordering = ["lesson", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "student"], name="lessons_attendance_unique_student"
            )
        ]
        indexes = [
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["student", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.student} @ {self.lesson_id}"

    @property
    def takes_seat(self) -> bool:
        return self.status in self.SEAT_TAKING_STATUSES

    @property
    def is_minor(self) -> bool:
        return student_is_minor(self.student, self.lesson.date if self.lesson_id else None)

    @property
    def level(self) -> str:
        return student_level(self.student)

    @property
    def needs_board(self) -> bool:
        return bool(self.lesson_id and self.lesson.lesson_type.requires_board)

    @property
    def needs_wetsuit(self) -> bool:
        return bool(self.lesson_id and self.lesson.lesson_type.requires_wetsuit)

    @property
    def equipment_ready(self) -> bool:
        """True when everything the lesson type demands has been handed out."""
        if self.needs_board and self.assigned_board_id is None:
            return False
        if self.needs_wetsuit and self.assigned_wetsuit_id is None:
            return False
        return True

    def clean(self) -> None:
        super().clean()
        if self.status == self.Status.CHECKED_IN and self.checked_in_at is None:
            self.checked_in_at = timezone.now()
        if (
            self.assigned_board_id
            and self.assigned_wetsuit_id
            and self.assigned_board_id == self.assigned_wetsuit_id
        ):
            raise ValidationError(
                {"assigned_wetsuit": _("The same item cannot be both the board and the wetsuit.")}
            )
