"""Booking and waiting-list models.

Design notes
------------
* A booking always belongs to a **customer** (the payer). ``student`` is the
  person who goes in the water; for a lesson booking it is mandatory, because
  every safety rule in :mod:`apps.bookings.services` is expressed per student.
* ``participants`` is the number of **seats** consumed. One booking for a family
  of four takes four seats but still names a single lead student.
* Money never moves here. This module keeps ``total_amount``/``paid_amount`` in
  sync so the operational screens can show a balance, while
  :mod:`apps.finance` remains the system of record for payments.

Cross-app expectations
----------------------
The adapter helpers at the bottom of this module are the *only* place that
reads another app's schedule fields, so a rename elsewhere is a one-line fix
here rather than a hunt through the service layer. They expect:

``lessons.Lesson``      ``start_time``/``end_time`` (datetime), ``status``,
                        ``max_students``, ``lesson_type``, ``instructor``
``lessons.LessonType``  ``color``, ``min_level``, ``max_level``,
                        ``duration_minutes``, ``max_students``
``surf_camps.SurfCamp`` ``start_date``/``end_date`` (date), ``status``,
                        ``max_participants``
``students.Student``    ``level`` (``SurfLevel``), ``date_of_birth``
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    BookingSource,
    BookingStatus,
    PaymentStatus,
    SurfLevel,
)
from apps.core.models import BaseModel, money_field
from apps.core.utils import next_sequential_code, to_decimal

ZERO = Decimal("0.00")

#: Default number of hours before the session start during which a customer may
#: still cancel without a fee. Overridable via the ``bookings.free_cancellation_hours``
#: system setting.
FREE_CANCELLATION_HOURS = 24

#: Fallback lesson length when neither the lesson nor its type declares one.
DEFAULT_LESSON_MINUTES = 120

#: Fallback seat count when a lesson does not declare a capacity.
DEFAULT_LESSON_CAPACITY = 8

#: Age below which the stricter supervision ratio applies.
MINOR_AGE = 18


# ---------------------------------------------------------------------------
# Cross-app adapters
# ---------------------------------------------------------------------------
def _first_attr(obj, *names, default=None):
    """Return the first non-empty attribute of *obj* among *names*."""
    if obj is None:
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return default


def as_aware(value):
    """Coerce a ``date`` or ``datetime`` into an aware datetime, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    if isinstance(value, date_cls):
        return timezone.make_aware(
            datetime.combine(value, time.min), timezone.get_current_timezone()
        )
    return None


def as_end_of_day(value):
    """Aware datetime at the very end of *value*'s day."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = timezone.localtime(as_aware(value)).date()
    return timezone.make_aware(
        datetime.combine(value, time.max), timezone.get_current_timezone()
    )


def _combine_with_lesson_date(lesson, value):
    """Turn a lesson's ``TimeField`` into an aware datetime.

    ``lessons.Lesson`` stores ``date`` and ``start_time``/``end_time``
    separately, so the raw attribute is a ``datetime.time`` with no day attached.
    Passing that straight to :func:`as_aware` returns ``None``, which reads as
    "this lesson has no scheduled time" and blocks every booking.
    """
    if not isinstance(value, time):
        return None
    day = _first_attr(lesson, "date", "lesson_date", "scheduled_date")
    if isinstance(day, datetime):
        day = timezone.localtime(as_aware(day)).date()
    if not isinstance(day, date_cls):
        return None
    return timezone.make_aware(datetime.combine(day, value), timezone.get_current_timezone())


def lesson_start(lesson):
    """Aware start datetime of a lesson."""
    raw = _first_attr(lesson, "start_time", "start_at", "starts_at", "scheduled_start")
    return _combine_with_lesson_date(lesson, raw) or as_aware(raw)


def lesson_end(lesson):
    """Aware end datetime of a lesson, derived from its duration when absent."""
    explicit = _first_attr(lesson, "end_time", "end_at", "ends_at", "scheduled_end")
    if explicit is not None:
        return _combine_with_lesson_date(lesson, explicit) or as_aware(explicit)
    start = lesson_start(lesson)
    if start is None:
        return None
    minutes = _first_attr(
        lesson,
        "duration_minutes",
        default=_first_attr(
            getattr(lesson, "lesson_type", None),
            "duration_minutes",
            default=DEFAULT_LESSON_MINUTES,
        ),
    )
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_LESSON_MINUTES
    return start + timedelta(minutes=max(1, minutes))


def lesson_capacity(lesson) -> int:
    """Number of seats the lesson offers."""
    value = _first_attr(lesson, "max_students", "max_participants", "capacity")
    if value is None:
        value = _first_attr(
            getattr(lesson, "lesson_type", None),
            "max_students",
            "max_participants",
            default=DEFAULT_LESSON_CAPACITY,
        )
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_LESSON_CAPACITY


def lesson_colour(lesson) -> str:
    """Hex colour used to paint the lesson on the calendar."""
    colour = _first_attr(getattr(lesson, "lesson_type", None), "color", "colour")
    colour = colour or _first_attr(lesson, "color", "colour")
    colour = str(colour or "#0ea5e9").strip()
    # Only ever emit a literal hex colour: this value reaches an inline style.
    if len(colour) in (4, 7) and colour.startswith("#"):
        if all(ch in "0123456789abcdefABCDEF" for ch in colour[1:]):
            return colour
    return "#0ea5e9"


def lesson_level_range(lesson) -> tuple[str | None, str | None]:
    """(minimum, maximum) surf level accepted by the lesson."""
    lesson_type = getattr(lesson, "lesson_type", None)
    minimum = _first_attr(lesson, "min_level", "minimum_level") or _first_attr(
        lesson_type, "min_level", "minimum_level"
    )
    maximum = _first_attr(lesson, "max_level", "maximum_level") or _first_attr(
        lesson_type, "max_level", "maximum_level"
    )
    return minimum, maximum


def lesson_target_level(lesson) -> str:
    """The level a lesson is pitched at — drives the instructor ratio."""
    minimum, maximum = lesson_level_range(lesson)
    return minimum or maximum or SurfLevel.BEGINNER


def camp_window(camp):
    """(start, end) aware datetimes covering an entire surf camp."""
    start = _first_attr(camp, "start_date", "starts_on", "start")
    end = _first_attr(camp, "end_date", "ends_on", "end") or start
    return as_aware(start), as_end_of_day(end)


def camp_capacity(camp) -> int:
    value = _first_attr(camp, "max_participants", "capacity", "max_students")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def student_age(student) -> int | None:
    """Age in whole years, or ``None`` when the birth date is unknown."""
    if student is None:
        return None
    explicit = getattr(student, "age", None)
    if isinstance(explicit, int):
        return explicit
    born = _first_attr(student, "date_of_birth", "birth_date", "born_on")
    if not isinstance(born, date_cls):
        return None
    today = timezone.localdate()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def is_minor(student) -> bool:
    age = student_age(student)
    return age is not None and age < MINOR_AGE


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------
class Booking(BaseModel):
    """One purchased place in the school's schedule."""

    class BookingType(models.TextChoices):
        LESSON = "lesson", _("Lesson")
        CAMP = "camp", _("Surf camp")
        RENTAL = "rental", _("Equipment rental")
        PACKAGE = "package", _("Lesson package")

    booking_code = models.CharField(
        _("booking code"),
        max_length=20,
        unique=True,
        db_index=True,
        help_text=_("Reference given to the customer, e.g. BK000001."),
    )
    booking_type = models.CharField(
        _("type"),
        max_length=10,
        choices=BookingType.choices,
        default=BookingType.LESSON,
        db_index=True,
    )

    # --- who ---------------------------------------------------------------
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="bookings",
        help_text=_("The person who pays for and owns this booking."),
    )
    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        help_text=_("The person who goes in the water. Required for lessons."),
    )

    # --- what --------------------------------------------------------------
    lesson = models.ForeignKey(
        "lessons.Lesson",
        verbose_name=_("lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    surf_camp = models.ForeignKey(
        "surf_camps.SurfCamp",
        verbose_name=_("surf camp"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )

    # --- state -------------------------------------------------------------
    status = models.CharField(
        _("status"),
        max_length=12,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True,
    )
    payment_status = models.CharField(
        _("payment status"),
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
        db_index=True,
    )
    participants = models.PositiveSmallIntegerField(
        _("participants"),
        default=1,
        help_text=_("Number of seats this booking occupies."),
    )

    # --- money -------------------------------------------------------------
    unit_price = money_field(_("price per participant"))
    discount_amount = money_field(_("discount"))
    total_amount = money_field(_("total"))
    paid_amount = money_field(_("paid"))

    # --- provenance --------------------------------------------------------
    source = models.CharField(
        _("source"),
        max_length=12,
        choices=BookingSource.choices,
        default=BookingSource.WALK_IN,
        db_index=True,
    )
    booked_at = models.DateTimeField(_("booked at"), default=timezone.now, db_index=True)
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)

    # --- cancellation ------------------------------------------------------
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.TextField(_("cancellation reason"), blank=True)
    cancellation_fee = money_field(_("cancellation fee"))

    # --- operations --------------------------------------------------------
    special_requests = models.TextField(
        _("special requests"),
        blank=True,
        help_text=_("Visible to the instructor: allergies, fears, kit sizes, languages."),
    )
    internal_notes = models.TextField(
        _("internal notes"), blank=True, help_text=_("Never shown to the customer.")
    )
    reminder_sent = models.BooleanField(_("reminder sent"), default=False)
    reminder_sent_at = models.DateTimeField(_("reminder sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("booking")
        verbose_name_plural = _("bookings")
        ordering = ["-booked_at", "-id"]
        indexes = [
            models.Index(fields=["status", "booked_at"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["payment_status", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_code} · {self.customer}"

    # -- persistence --------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.booking_code:
            self.booking_code = next_sequential_code(
                Booking, "booking_code", "BK", width=6
            )
        super().save(*args, **kwargs)

    # -- schedule -----------------------------------------------------------
    @property
    def scheduled_start(self):
        """Aware datetime the booked activity starts, or ``None``."""
        if self.lesson_id:
            return lesson_start(self.lesson)
        if self.surf_camp_id:
            return camp_window(self.surf_camp)[0]
        return None

    @property
    def scheduled_end(self):
        if self.lesson_id:
            return lesson_end(self.lesson)
        if self.surf_camp_id:
            return camp_window(self.surf_camp)[1]
        return None

    @property
    def activity_label(self) -> str:
        """Human name of what was booked."""
        if self.lesson_id:
            return str(self.lesson)
        if self.surf_camp_id:
            return str(self.surf_camp)
        return str(self.get_booking_type_display())

    # -- money --------------------------------------------------------------
    @property
    def balance_due(self) -> Decimal:
        """Amount still owed. Negative means the customer is in credit."""
        return to_decimal(self.total_amount) - to_decimal(self.paid_amount)

    @property
    def is_paid(self) -> bool:
        total = to_decimal(self.total_amount)
        return to_decimal(self.paid_amount) >= total and total >= ZERO

    @property
    def is_overpaid(self) -> bool:
        return self.balance_due < ZERO

    @property
    def gross_amount(self) -> Decimal:
        """Price before discount — what the customer would have paid at rack rate."""
        return to_decimal(self.unit_price) * max(1, int(self.participants or 1))

    # -- lifecycle ----------------------------------------------------------
    @property
    def is_active(self) -> bool:
        """The booking still occupies a seat."""
        return self.status in ACTIVE_BOOKING_STATUSES

    @property
    def can_cancel(self) -> bool:
        return self.status in {
            BookingStatus.DRAFT,
            BookingStatus.PENDING,
            BookingStatus.CONFIRMED,
            BookingStatus.CHECKED_IN,
        }

    @property
    def can_confirm(self) -> bool:
        return self.status in {BookingStatus.DRAFT, BookingStatus.PENDING}

    @property
    def can_check_in(self) -> bool:
        return self.status == BookingStatus.CONFIRMED

    @property
    def can_complete(self) -> bool:
        return self.status in {BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN}

    @property
    def can_mark_no_show(self) -> bool:
        return self.status in {BookingStatus.CONFIRMED, BookingStatus.PENDING}

    @property
    def is_cancellable_free(self) -> bool:
        """True when cancelling now costs the customer nothing.

        Free until ``FREE_CANCELLATION_HOURS`` before the session starts.
        Bookings with no scheduled start (rentals, open packages) are always
        free to cancel.
        """
        start = self.scheduled_start
        if start is None:
            return True
        return start - timezone.now() >= timedelta(hours=FREE_CANCELLATION_HOURS)

    @property
    def hours_until_start(self) -> float | None:
        start = self.scheduled_start
        if start is None:
            return None
        return round((start - timezone.now()).total_seconds() / 3600.0, 1)

    @property
    def is_past(self) -> bool:
        end = self.scheduled_end
        return bool(end and end < timezone.now())

    # -- calculations -------------------------------------------------------
    def recalculate_totals(self, commit: bool = False) -> Decimal:
        """Recompute ``total_amount`` and ``payment_status`` from the parts.

        A cancelled booking owes only its cancellation fee — that is what makes
        the finance screens agree with the operations screens after a
        cancellation.
        """
        participants = max(1, int(self.participants or 1))
        if self.status == BookingStatus.CANCELLED:
            gross = to_decimal(self.cancellation_fee)
        else:
            gross = to_decimal(self.unit_price) * participants - to_decimal(
                self.discount_amount
            )
        total = to_decimal(max(ZERO, gross))
        self.total_amount = total

        paid = to_decimal(self.paid_amount)
        if self.payment_status != PaymentStatus.REFUNDED:
            if total <= ZERO or paid >= total:
                self.payment_status = PaymentStatus.PAID
            elif paid <= ZERO:
                self.payment_status = (
                    PaymentStatus.OVERDUE if self.is_past else PaymentStatus.UNPAID
                )
            else:
                self.payment_status = (
                    PaymentStatus.OVERDUE if self.is_past else PaymentStatus.PARTIAL
                )

        if commit:
            self.save(update_fields=["total_amount", "payment_status", "updated_at"])
        return total

    # -- validation ---------------------------------------------------------
    def clean(self):
        errors: dict[str, list] = {}

        def add(field, message):
            errors.setdefault(field, []).append(message)

        if self.lesson_id and self.surf_camp_id:
            add(
                "surf_camp",
                _("A booking is either for a lesson or for a surf camp, not both."),
            )

        if self.booking_type == self.BookingType.LESSON:
            if not self.lesson_id:
                add("lesson", _("A lesson booking must reference a lesson."))
            if not self.student_id:
                add("student", _("A lesson booking must name the student who will attend."))
        elif self.booking_type == self.BookingType.CAMP:
            if not self.surf_camp_id:
                add("surf_camp", _("A surf-camp booking must reference a surf camp."))

        if int(self.participants or 0) < 1:
            add("participants", _("A booking must have at least one participant."))

        if to_decimal(self.discount_amount) < ZERO:
            add("discount_amount", _("A discount cannot be negative."))
        elif to_decimal(self.discount_amount) > self.gross_amount:
            add("discount_amount", _("The discount cannot exceed the price of the booking."))

        if to_decimal(self.paid_amount) < ZERO:
            add("paid_amount", _("The paid amount cannot be negative."))

        if self.status == BookingStatus.CANCELLED and not self.cancellation_reason.strip():
            add("cancellation_reason", _("Record why the booking was cancelled."))

        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Waiting list
# ---------------------------------------------------------------------------
class WaitlistEntry(BaseModel):
    """A customer waiting for a seat that is currently sold out.

    When a booking is cancelled the first entry in the queue is promoted into a
    held (pending) booking, so a freed seat never quietly disappears.
    """

    lesson = models.ForeignKey(
        "lessons.Lesson",
        verbose_name=_("lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entries",
    )
    surf_camp = models.ForeignKey(
        "surf_camps.SurfCamp",
        verbose_name=_("surf camp"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entries",
    )
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="waitlist_entries",
    )
    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entries",
    )

    requested_at = models.DateTimeField(_("requested at"), default=timezone.now, db_index=True)
    participants = models.PositiveSmallIntegerField(_("seats wanted"), default=1)
    position = models.PositiveIntegerField(_("position"), default=0, db_index=True)

    is_notified = models.BooleanField(_("notified"), default=False)
    notified_at = models.DateTimeField(_("notified at"), null=True, blank=True)
    is_converted = models.BooleanField(_("converted"), default=False, db_index=True)
    converted_booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("resulting booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_conversions",
    )
    note = models.CharField(_("note"), max_length=250, blank=True)

    class Meta:
        verbose_name = _("waiting-list entry")
        verbose_name_plural = _("waiting list")
        ordering = ["requested_at"]
        indexes = [
            models.Index(fields=["lesson", "is_converted", "position"]),
            models.Index(fields=["surf_camp", "is_converted", "position"]),
        ]

    def __str__(self) -> str:
        target = self.lesson or self.surf_camp
        return f"#{self.position} {self.customer} → {target}"

    @property
    def is_waiting(self) -> bool:
        return not self.is_converted

    def clean(self):
        if not self.lesson_id and not self.surf_camp_id:
            raise ValidationError(
                {"lesson": _("Choose the lesson or the surf camp being waited for.")}
            )
        if self.lesson_id and self.surf_camp_id:
            raise ValidationError(
                {"surf_camp": _("An entry waits for a lesson or a surf camp, not both.")}
            )
        if int(self.participants or 0) < 1:
            raise ValidationError({"participants": _("At least one seat must be requested.")})

    def save(self, *args, **kwargs):
        if not self.position:
            queryset = WaitlistEntry.objects.all()
            if self.lesson_id:
                queryset = queryset.filter(lesson_id=self.lesson_id)
            elif self.surf_camp_id:
                queryset = queryset.filter(surf_camp_id=self.surf_camp_id)
            self.position = queryset.count() + 1
        super().save(*args, **kwargs)
