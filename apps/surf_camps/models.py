"""Surf camp models.

A surf camp is a multi-day package: accommodation + meals + transfer + a
day-by-day programme of lessons and activities, sold at one price.

Design notes
------------
* A camp is **not** an opaque product. Every day is a :class:`CampDay` row and
  every session on that day is a :class:`CampActivity` row, because each session
  still needs an instructor, a ratio check and possibly a different beach — the
  surf spot changes with the forecast, which is why ``CampDay.spot`` exists and
  overrides ``SurfCamp.spot`` for that day.
* Seat accounting lives on :class:`CampParticipant`. A cancelled participant no
  longer occupies a place, which is what makes ``available_places`` correct
  after a cancellation.
* Money is always :func:`~apps.core.models.money_field` (Decimal 12,2).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel, level_rank
from apps.core.models import BaseModel, money_field
from apps.core.validators import validate_image_upload

ZERO = Decimal("0.00")


class CampStatus(models.TextChoices):
    """Lifecycle of a camp product."""

    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    FULL = "full", _("Fully booked")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


#: Statuses in which the camp still accepts (or holds) registrations.
OPEN_CAMP_STATUSES: tuple[str, ...] = (
    CampStatus.DRAFT,
    CampStatus.PUBLISHED,
    CampStatus.FULL,
    CampStatus.RUNNING,
)

#: Statuses that mean "this camp is over or called off".
CLOSED_CAMP_STATUSES: tuple[str, ...] = (CampStatus.COMPLETED, CampStatus.CANCELLED)


class ParticipantStatus(models.TextChoices):
    """Where a participant is in the arrival → departure flow."""

    REGISTERED = "registered", _("Registered")
    CONFIRMED = "confirmed", _("Confirmed")
    ARRIVED = "arrived", _("Arrived")
    DEPARTED = "departed", _("Departed")
    CANCELLED = "cancelled", _("Cancelled")


#: Statuses that still occupy a place in the camp.
ACTIVE_PARTICIPANT_STATUSES: tuple[str, ...] = (
    ParticipantStatus.REGISTERED,
    ParticipantStatus.CONFIRMED,
    ParticipantStatus.ARRIVED,
    ParticipantStatus.DEPARTED,
)

#: Statuses that mean the person is physically on site.
ON_SITE_PARTICIPANT_STATUSES: tuple[str, ...] = (ParticipantStatus.ARRIVED,)


class RoomType(models.TextChoices):
    SINGLE = "single", _("Single room")
    DOUBLE = "double", _("Double room")
    SHARED = "shared", _("Shared room / dorm")


class ActivityType(models.TextChoices):
    SURF_LESSON = "surf_lesson", _("Surf lesson")
    THEORY = "theory", _("Theory session")
    VIDEO_ANALYSIS = "video_analysis", _("Video analysis")
    YOGA = "yoga", _("Yoga")
    FITNESS = "fitness", _("Fitness / warm-up")
    EXCURSION = "excursion", _("Excursion")
    MEAL = "meal", _("Meal")
    FREE_TIME = "free_time", _("Free time")
    TRANSFER = "transfer", _("Transfer")
    SOCIAL = "social", _("Social event")
    OTHER = "other", _("Other")


#: Activity types that put people in the water — these drive the ratio checks.
WATER_ACTIVITY_TYPES: tuple[str, ...] = (ActivityType.SURF_LESSON,)


class TShirtSize(models.TextChoices):
    XS = "xs", _("XS")
    S = "s", _("S")
    M = "m", _("M")
    L = "l", _("L")
    XL = "xl", _("XL")
    XXL = "xxl", _("XXL")


# ---------------------------------------------------------------------------
# Camp
# ---------------------------------------------------------------------------
class SurfCamp(BaseModel):
    """A dated, priced, multi-day surf camp."""

    Status = CampStatus

    name = models.CharField(_("name"), max_length=200, db_index=True)
    code = models.CharField(
        _("camp code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Generated automatically (CAMP001) when left blank."),
    )
    description = models.TextField(_("description"), blank=True)
    photo = models.ImageField(
        _("photo"),
        upload_to="surf_camps/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )

    start_date = models.DateField(_("start date"), db_index=True)
    end_date = models.DateField(_("end date"), db_index=True)
    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("home surf spot"),
        on_delete=models.PROTECT,
        related_name="surf_camps",
        help_text=_("Default spot. Each camp day may override it as the forecast changes."),
    )

    capacity = models.PositiveIntegerField(
        _("capacity"), default=12, validators=[MinValueValidator(1)]
    )
    min_participants = models.PositiveIntegerField(
        _("minimum participants"),
        default=1,
        help_text=_("Below this number the camp is not economically viable."),
    )

    min_level = models.CharField(
        _("minimum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.FIRST_TIME,
        db_index=True,
    )
    max_level = models.CharField(
        _("maximum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.ADVANCED,
        db_index=True,
    )

    price = money_field(_("price per person"))
    deposit_amount = money_field(_("deposit"))
    single_room_supplement = money_field(_("single room supplement"))

    includes_accommodation = models.BooleanField(_("accommodation included"), default=True)
    includes_meals = models.BooleanField(_("meals included"), default=True)
    includes_transfer = models.BooleanField(_("airport transfer included"), default=False)
    includes_equipment = models.BooleanField(_("equipment included"), default=True)
    includes_insurance = models.BooleanField(_("insurance included"), default=False)

    accommodation_name = models.CharField(_("accommodation"), max_length=200, blank=True)
    accommodation_address = models.TextField(_("accommodation address"), blank=True)
    meal_plan = models.TextField(
        _("meal plan"), blank=True, help_text=_("For example: breakfast + lunch, 7 days.")
    )

    transfer_pickup_point = models.CharField(_("pick-up point"), max_length=200, blank=True)
    transfer_notes = models.TextField(_("transfer notes"), blank=True)

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=CampStatus.choices,
        default=CampStatus.DRAFT,
        db_index=True,
    )

    lead_instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("lead instructor"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_camps",
    )
    instructors = models.ManyToManyField(
        "instructors.Instructor",
        verbose_name=_("instructors"),
        blank=True,
        related_name="camps",
    )

    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("surf camp")
        verbose_name_plural = _("surf camps")
        ordering = ["-start_date", "name"]
        indexes = [
            models.Index(fields=["status", "start_date"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["is_active", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="surf_camp_end_date_after_start_date",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gte=1), name="surf_camp_capacity_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}" if self.code else self.name

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors.setdefault("end_date", []).append(
                str(_("The end date cannot be before the start date."))
            )

        if self.capacity is not None and self.capacity <= 0:
            errors.setdefault("capacity", []).append(
                str(_("Capacity must be at least one place."))
            )

        if (
            self.capacity is not None
            and self.min_participants is not None
            and self.min_participants > self.capacity
        ):
            errors.setdefault("min_participants", []).append(
                str(_("The minimum cannot exceed the capacity."))
            )

        if self.min_level and self.max_level and level_rank(self.min_level) > level_rank(
            self.max_level
        ):
            errors.setdefault("max_level", []).append(
                str(_("The maximum level must not be below the minimum level."))
            )

        for field in ("price", "deposit_amount", "single_room_supplement"):
            value = getattr(self, field, None)
            if value is not None and value < ZERO:
                errors.setdefault(field, []).append(str(_("This value cannot be negative.")))

        if (
            self.price is not None
            and self.deposit_amount is not None
            and self.deposit_amount > self.price
        ):
            errors.setdefault("deposit_amount", []).append(
                str(_("The deposit cannot exceed the full price."))
            )

        # Shrinking capacity below the number of people already booked would
        # silently overbook the camp.
        if self.pk and self.capacity is not None:
            booked = self.participant_count
            if booked > self.capacity:
                errors.setdefault("capacity", []).append(
                    str(
                        _("%(booked)s people are already registered; capacity cannot be lower.")
                        % {"booked": booked}
                    )
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.code:
            from apps.core.utils import next_sequential_code

            self.code = next_sequential_code(SurfCamp, "code", "CAMP", width=3)
        super().save(*args, **kwargs)

    # -- querysets ---------------------------------------------------------
    def active_participants(self):
        """Participants that still occupy a place."""
        return self.participants.filter(status__in=ACTIVE_PARTICIPANT_STATUSES)

    # -- properties --------------------------------------------------------
    @property
    def duration_days(self) -> int:
        """Number of calendar days, inclusive of both ends."""
        if not self.start_date or not self.end_date:
            return 0
        return (self.end_date - self.start_date).days + 1

    @property
    def nights(self) -> int:
        return max(self.duration_days - 1, 0)

    @property
    def participant_count(self) -> int:
        return self.active_participants().count()

    @property
    def available_places(self) -> int:
        return max((self.capacity or 0) - self.participant_count, 0)

    @property
    def is_full(self) -> bool:
        return self.participant_count >= (self.capacity or 0)

    @property
    def occupancy_rate(self) -> float:
        """Filled places as a percentage of capacity."""
        if not self.capacity:
            return 0.0
        return round((self.participant_count / self.capacity) * 100, 1)

    @property
    def reaches_minimum(self) -> bool:
        return self.participant_count >= (self.min_participants or 0)

    @property
    def total_revenue(self) -> Decimal:
        """Gross value of the places currently sold, including supplements."""
        totals = self.active_participants().aggregate(
            people=Count("id"),
            singles=Count("id", filter=Q(room_type=RoomType.SINGLE)),
        )
        people = totals["people"] or 0
        singles = totals["singles"] or 0
        price = self.price or ZERO
        supplement = self.single_room_supplement or ZERO
        return (price * people) + (supplement * singles)

    @property
    def potential_revenue(self) -> Decimal:
        """Gross value if every place were sold at the standard price."""
        return (self.price or ZERO) * (self.capacity or 0)

    @property
    def is_upcoming(self) -> bool:
        today = timezone.localdate()
        return bool(
            self.start_date
            and self.start_date > today
            and self.status not in CLOSED_CAMP_STATUSES
        )

    @property
    def is_running(self) -> bool:
        today = timezone.localdate()
        return bool(
            self.start_date
            and self.end_date
            and self.start_date <= today <= self.end_date
            and self.status not in CLOSED_CAMP_STATUSES
        )

    @property
    def is_past(self) -> bool:
        return bool(self.end_date and self.end_date < timezone.localdate())

    @property
    def days_until_start(self) -> int | None:
        if not self.start_date:
            return None
        return (self.start_date - timezone.localdate()).days

    @property
    def level_label(self) -> str:
        """``Beginner → Intermediate``, or a single level when both match."""
        low = SurfLevel(self.min_level).label if self.min_level else ""
        high = SurfLevel(self.max_level).label if self.max_level else ""
        if low and high and low != high:
            return f"{low} → {high}"
        return low or high

    def accepts_level(self, level: str | None) -> bool:
        """Is *level* inside this camp's advertised range?"""
        if not level:
            return False
        return level_rank(self.min_level) <= level_rank(level) <= level_rank(self.max_level)

    def date_list(self) -> list[date]:
        """Every calendar date the camp spans."""
        if not self.start_date or not self.end_date:
            return []
        span = (self.end_date - self.start_date).days
        return [self.start_date + timedelta(days=offset) for offset in range(span + 1)]


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
class CampParticipant(BaseModel):
    """One person's place in a camp, with their logistics and balance."""

    Status = ParticipantStatus
    RoomType = RoomType

    camp = models.ForeignKey(
        SurfCamp,
        verbose_name=_("camp"),
        on_delete=models.CASCADE,
        related_name="participants",
    )
    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.PROTECT,
        related_name="camp_participations",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_participations",
    )

    room_number = models.CharField(_("room number"), max_length=20, blank=True)
    room_type = models.CharField(
        _("room type"), max_length=10, choices=RoomType.choices, default=RoomType.SHARED
    )
    roommate_preference = models.CharField(_("roommate preference"), max_length=200, blank=True)

    arrival_datetime = models.DateTimeField(_("arrival"), null=True, blank=True)
    departure_datetime = models.DateTimeField(_("departure"), null=True, blank=True)
    arrival_flight = models.CharField(_("arrival flight"), max_length=40, blank=True)
    departure_flight = models.CharField(_("departure flight"), max_length=40, blank=True)

    needs_transfer = models.BooleanField(_("needs transfer"), default=False, db_index=True)
    dietary_requirements = models.CharField(_("dietary requirements"), max_length=200, blank=True)
    medical_notes = models.TextField(
        _("medical notes"),
        blank=True,
        help_text=_("Visible to instructors and lifeguards on the daily roster."),
    )
    t_shirt_size = models.CharField(
        _("t-shirt size"), max_length=5, choices=TShirtSize.choices, blank=True
    )

    amount_paid = money_field(
        _("amount paid"),
        help_text=_("Total collected for this place; mirrors the payments in Finance."),
    )
    deposit_paid = models.BooleanField(_("deposit paid"), default=False)

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ParticipantStatus.choices,
        default=ParticipantStatus.REGISTERED,
        db_index=True,
    )
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("camp participant")
        verbose_name_plural = _("camp participants")
        ordering = ["camp", "student"]
        indexes = [
            models.Index(fields=["camp", "status"]),
            models.Index(fields=["status", "needs_transfer"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["camp", "student"],
                condition=Q(is_deleted=False),
                name="unique_active_participant_per_camp",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} · {self.camp}"

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        if (
            self.arrival_datetime
            and self.departure_datetime
            and self.departure_datetime < self.arrival_datetime
        ):
            errors.setdefault("departure_datetime", []).append(
                str(_("Departure cannot be before arrival."))
            )

        if self.amount_paid is not None and self.amount_paid < ZERO:
            errors.setdefault("amount_paid", []).append(
                str(_("This value cannot be negative."))
            )

        if self.needs_transfer and not self.arrival_datetime:
            errors.setdefault("arrival_datetime", []).append(
                str(_("An arrival time is required to plan the transfer."))
            )

        if errors:
            raise ValidationError(errors)

    # -- properties --------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_PARTICIPANT_STATUSES

    @property
    def total_price(self) -> Decimal:
        """Camp price plus the single-room supplement when applicable."""
        price = (self.camp.price or ZERO) if self.camp_id else ZERO
        if self.room_type == RoomType.SINGLE and self.camp_id:
            price += self.camp.single_room_supplement or ZERO
        return price

    @property
    def balance_due(self) -> Decimal:
        return max(self.total_price - (self.amount_paid or ZERO), ZERO)

    @property
    def is_fully_paid(self) -> bool:
        return self.balance_due <= ZERO

    @property
    def payment_state(self) -> str:
        """One of the shared :class:`~apps.core.enums.PaymentStatus` values."""
        from apps.core.enums import PaymentStatus

        paid = self.amount_paid or ZERO
        if paid <= ZERO:
            return PaymentStatus.UNPAID
        if paid >= self.total_price:
            return PaymentStatus.PAID
        return PaymentStatus.PARTIAL

    @property
    def has_medical_flag(self) -> bool:
        return bool(self.medical_notes.strip())

    def is_on_site(self, on_date: date) -> bool:
        """Is this participant present on *on_date*?

        Arrival/departure times are optional: without them we fall back to the
        camp dates, because a participant with no flight details is assumed to
        be there for the whole camp.
        """
        if self.status not in ACTIVE_PARTICIPANT_STATUSES:
            return False
        if self.status == ParticipantStatus.CANCELLED:
            return False
        start = (
            timezone.localtime(self.arrival_datetime).date()
            if self.arrival_datetime
            else self.camp.start_date
        )
        end = (
            timezone.localtime(self.departure_datetime).date()
            if self.departure_datetime
            else self.camp.end_date
        )
        if start is None or end is None:
            return True
        return start <= on_date <= end


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
class CampDay(BaseModel):
    """One calendar day of a camp, carrying that day's programme."""

    camp = models.ForeignKey(
        SurfCamp, verbose_name=_("camp"), on_delete=models.CASCADE, related_name="days"
    )
    date = models.DateField(_("date"), db_index=True)
    day_number = models.PositiveIntegerField(_("day number"), default=1)
    title = models.CharField(_("title"), max_length=150, blank=True)
    description = models.TextField(_("description"), blank=True)
    weather_note = models.TextField(_("weather note"), blank=True)
    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("spot for the day"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_days",
        help_text=_("Leave empty to use the camp's home spot."),
    )

    class Meta:
        verbose_name = _("camp day")
        verbose_name_plural = _("camp days")
        ordering = ["date"]
        indexes = [models.Index(fields=["camp", "date"])]
        constraints = [
            models.UniqueConstraint(
                fields=["camp", "date"],
                condition=Q(is_deleted=False),
                name="unique_active_day_per_camp",
            ),
        ]

    def __str__(self) -> str:
        label = self.title or _("Day %(n)s") % {"n": self.day_number}
        return f"{self.camp.code} · {label}"

    def clean(self) -> None:
        super().clean()
        if self.camp_id and self.date:
            if self.date < self.camp.start_date or self.date > self.camp.end_date:
                raise ValidationError(
                    {"date": _("The date must fall inside the camp's dates.")}
                )

    @property
    def effective_spot(self):
        """The spot actually used on this day."""
        return self.spot or (self.camp.spot if self.camp_id else None)

    @property
    def is_today(self) -> bool:
        return self.date == timezone.localdate()

    @property
    def is_past(self) -> bool:
        return bool(self.date and self.date < timezone.localdate())

    @property
    def display_title(self) -> str:
        return self.title or str(_("Day %(n)s") % {"n": self.day_number})


class CampActivity(BaseModel):
    """A single scheduled item inside a camp day."""

    ActivityType = ActivityType

    camp_day = models.ForeignKey(
        CampDay, verbose_name=_("camp day"), on_delete=models.CASCADE, related_name="activities"
    )
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    title = models.CharField(_("title"), max_length=150)
    activity_type = models.CharField(
        _("type"),
        max_length=20,
        choices=ActivityType.choices,
        default=ActivityType.OTHER,
        db_index=True,
    )
    instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("instructor"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_activities",
    )
    lesson = models.ForeignKey(
        "lessons.Lesson",
        verbose_name=_("linked lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_activities",
        help_text=_("Set when this activity is a lesson scheduled in the lessons module."),
    )
    location = models.CharField(_("location"), max_length=150, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("camp activity")
        verbose_name_plural = _("camp activities")
        ordering = ["start_time", "id"]
        indexes = [
            models.Index(fields=["camp_day", "start_time"]),
            models.Index(fields=["activity_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="camp_activity_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.start_time:%H:%M} {self.title}"

    def clean(self) -> None:
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("The end time must be after the start time.")})

    @property
    def duration_minutes(self) -> int:
        if not self.start_time or not self.end_time:
            return 0
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(end - start, 0)

    @property
    def is_water_activity(self) -> bool:
        return self.activity_type in WATER_ACTIVITY_TYPES

    @property
    def time_label(self) -> str:
        return f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def overlaps(self, other: CampActivity) -> bool:
        """Do two activities on the same day share any minute?"""
        return self.start_time < other.end_time and other.start_time < self.end_time
