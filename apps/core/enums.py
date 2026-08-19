"""Shared enumerations.

Every module imports these instead of redefining its own status vocabulary, so
a booking's "confirmed" means exactly the same thing everywhere and badge
colours stay consistent across screens.

Numeric thresholds attached to the surf enums come from the domain research in
``docs/research/SURF_DOMAIN_MODEL.md``.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# People & skill
# ---------------------------------------------------------------------------
class SurfLevel(models.TextChoices):
    FIRST_TIME = "first_time", _("First Time")
    BEGINNER = "beginner", _("Beginner")
    ADVANCED_BEGINNER = "advanced_beginner", _("Advanced Beginner")
    INTERMEDIATE = "intermediate", _("Intermediate")
    ADVANCED = "advanced", _("Advanced")
    COMPETITION = "competition", _("Competition")


#: Ordinal rank of each level, for "is this student good enough?" comparisons.
SURF_LEVEL_ORDER: dict[str, int] = {
    SurfLevel.FIRST_TIME: 0,
    SurfLevel.BEGINNER: 1,
    SurfLevel.ADVANCED_BEGINNER: 2,
    SurfLevel.INTERMEDIATE: 3,
    SurfLevel.ADVANCED: 4,
    SurfLevel.COMPETITION: 5,
}


def level_rank(level: str) -> int:
    return SURF_LEVEL_ORDER.get(level, 0)


#: Maximum students per instructor by level — a SAFETY constraint, not a
#: preference. Beginners in the water need close supervision.
MAX_STUDENTS_PER_INSTRUCTOR: dict[str, int] = {
    SurfLevel.FIRST_TIME: 6,
    SurfLevel.BEGINNER: 8,
    SurfLevel.ADVANCED_BEGINNER: 8,
    SurfLevel.INTERMEDIATE: 10,
    SurfLevel.ADVANCED: 10,
    SurfLevel.COMPETITION: 6,
}

#: Stricter ratio for under-18 groups.
MAX_STUDENTS_PER_INSTRUCTOR_MINORS: int = 6


class Gender(models.TextChoices):
    FEMALE = "female", _("Female")
    MALE = "male", _("Male")
    OTHER = "other", _("Other")
    UNDISCLOSED = "undisclosed", _("Prefer not to say")


# ---------------------------------------------------------------------------
# Booking & money
# ---------------------------------------------------------------------------
class BookingStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PENDING = "pending", _("Pending confirmation")
    CONFIRMED = "confirmed", _("Confirmed")
    CHECKED_IN = "checked_in", _("Checked in")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    NO_SHOW = "no_show", _("No show")


#: Statuses that still occupy a seat / an instructor slot.
ACTIVE_BOOKING_STATUSES = (
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.CHECKED_IN,
)


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", _("Unpaid")
    PARTIAL = "partial", _("Partially paid")
    PAID = "paid", _("Paid")
    REFUNDED = "refunded", _("Refunded")
    OVERDUE = "overdue", _("Overdue")


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    CARD = "card", _("Credit / debit card")
    TRANSFER = "transfer", _("Bank transfer")
    ONLINE = "online", _("Online payment")
    PACKAGE = "package", _("Lesson package")
    VOUCHER = "voucher", _("Voucher")
    OTHER = "other", _("Other")


class BookingSource(models.TextChoices):
    WALK_IN = "walk_in", _("Walk-in")
    PHONE = "phone", _("Phone")
    EMAIL = "email", _("E-mail")
    WEBSITE = "website", _("Website")
    PARTNER = "partner", _("Partner / agency")
    SOCIAL = "social", _("Social media")
    RETURNING = "returning", _("Returning customer")
    OTHER = "other", _("Other")


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------
class EquipmentStatus(models.TextChoices):
    AVAILABLE = "available", _("Available")
    RENTED = "rented", _("Rented out")
    IN_LESSON = "in_lesson", _("In use — lesson")
    RESERVED = "reserved", _("Reserved")
    MAINTENANCE = "maintenance", _("In maintenance")
    DAMAGED = "damaged", _("Damaged")
    LOST = "lost", _("Lost")
    RETIRED = "retired", _("Retired")


#: Statuses in which a piece of equipment may NOT be handed to a customer.
UNAVAILABLE_EQUIPMENT_STATUSES = (
    EquipmentStatus.MAINTENANCE,
    EquipmentStatus.DAMAGED,
    EquipmentStatus.LOST,
    EquipmentStatus.RETIRED,
)


class EquipmentCondition(models.TextChoices):
    NEW = "new", _("New")
    EXCELLENT = "excellent", _("Excellent")
    GOOD = "good", _("Good")
    FAIR = "fair", _("Fair")
    POOR = "poor", _("Poor")
    UNUSABLE = "unusable", _("Unusable")


class RentalPeriod(models.TextChoices):
    HOURLY = "hourly", _("Hourly")
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")


class DamageType(models.TextChoices):
    DING = "ding", _("Ding")
    CRACK = "crack", _("Crack")
    FIN_DAMAGE = "fin_damage", _("Fin damage")
    LEASH_DAMAGE = "leash_damage", _("Leash damage")
    WETSUIT_TEAR = "wetsuit_tear", _("Wetsuit tear")
    ZIPPER = "zipper", _("Zipper failure")
    WATER_DAMAGE = "water_damage", _("Water damage / delamination")
    DELAMINATION = "delamination", _("Delamination")
    SNAPPED = "snapped", _("Snapped board")
    GENERAL = "general", _("General maintenance")
    OTHER = "other", _("Other")


# ---------------------------------------------------------------------------
# Surf conditions
# ---------------------------------------------------------------------------
class TideState(models.TextChoices):
    LOW = "low", _("Low tide")
    MID_RISING = "mid_rising", _("Mid — rising")
    HIGH = "high", _("High tide")
    MID_FALLING = "mid_falling", _("Mid — falling")
    UNKNOWN = "unknown", _("Unknown")


class WindType(models.TextChoices):
    OFFSHORE = "offshore", _("Offshore")
    CROSS_OFFSHORE = "cross_offshore", _("Cross-offshore")
    CROSS_SHORE = "cross_shore", _("Cross-shore")
    CROSS_ONSHORE = "cross_onshore", _("Cross-onshore")
    ONSHORE = "onshore", _("Onshore")
    GLASSY = "glassy", _("Glassy / no wind")


class BreakType(models.TextChoices):
    BEACH_BREAK = "beach_break", _("Beach break")
    POINT_BREAK = "point_break", _("Point break")
    REEF_BREAK = "reef_break", _("Reef break")
    RIVER_MOUTH = "river_mouth", _("River mouth")


class BottomType(models.TextChoices):
    SAND = "sand", _("Sand")
    REEF = "reef", _("Reef")
    ROCK = "rock", _("Rock")
    MIXED = "mixed", _("Mixed")


#: Wave height (metres) considered suitable per level: (min, ideal_low,
#: ideal_high, max_safe). Above ``max_safe`` the level must not be in the water.
WAVE_HEIGHT_SUITABILITY: dict[str, tuple[float, float, float, float]] = {
    SurfLevel.FIRST_TIME: (0.2, 0.3, 0.7, 1.0),
    SurfLevel.BEGINNER: (0.2, 0.4, 0.9, 1.2),
    SurfLevel.ADVANCED_BEGINNER: (0.3, 0.5, 1.2, 1.5),
    SurfLevel.INTERMEDIATE: (0.4, 0.8, 1.8, 2.5),
    SurfLevel.ADVANCED: (0.5, 1.2, 3.0, 4.0),
    SurfLevel.COMPETITION: (0.5, 1.2, 3.5, 5.0),
}

#: Wind speed (km/h) above which a lesson at each level becomes unsafe.
MAX_WIND_KMH: dict[str, float] = {
    SurfLevel.FIRST_TIME: 25.0,
    SurfLevel.BEGINNER: 30.0,
    SurfLevel.ADVANCED_BEGINNER: 35.0,
    SurfLevel.INTERMEDIATE: 40.0,
    SurfLevel.ADVANCED: 50.0,
    SurfLevel.COMPETITION: 50.0,
}

#: Water temperature (°C) -> recommended wetsuit. Source: standard industry
#: thickness tables (see docs/research/SURF_DOMAIN_MODEL.md).
WETSUIT_BY_WATER_TEMP: tuple[tuple[float, float, str], ...] = (
    (24.0, 99.0, "boardshorts / swimsuit"),
    (22.0, 24.0, "1mm top or shorty"),
    (19.0, 22.0, "2mm shorty"),
    (17.0, 19.0, "3/2 mm full suit"),
    (14.0, 17.0, "4/3 mm full suit"),
    (11.0, 14.0, "4/3 mm + boots"),
    (9.0, 11.0, "5/4 mm + boots + gloves"),
    (-5.0, 9.0, "6/5 mm + boots + gloves + hood"),
)

#: Board volume coefficient (litres per kg of rider weight) by level.
#: volume_litres ≈ weight_kg × coefficient.
BOARD_VOLUME_COEFFICIENT: dict[str, float] = {
    SurfLevel.FIRST_TIME: 1.00,
    SurfLevel.BEGINNER: 0.85,
    SurfLevel.ADVANCED_BEGINNER: 0.70,
    SurfLevel.INTERMEDIATE: 0.55,
    SurfLevel.ADVANCED: 0.42,
    SurfLevel.COMPETITION: 0.36,
}


# ---------------------------------------------------------------------------
# Safety & operations
# ---------------------------------------------------------------------------
class Severity(models.TextChoices):
    LOW = "low", _("Low")
    MEDIUM = "medium", _("Medium")
    HIGH = "high", _("High")
    CRITICAL = "critical", _("Critical")


class BeachFlag(models.TextChoices):
    GREEN = "green", _("Green — low hazard")
    YELLOW = "yellow", _("Yellow — medium hazard")
    RED = "red", _("Red — high hazard, no swimming")
    DOUBLE_RED = "double_red", _("Double red — water closed")
    PURPLE = "purple", _("Purple — dangerous marine life")
    BLACK_WHITE = "black_white", _("Black & white — surfing area")


class LessonStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    CONFIRMED = "confirmed", _("Confirmed")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    POSTPONED = "postponed", _("Postponed — conditions")


class GenericStatus(models.TextChoices):
    """For simple workflow records (maintenance, incidents, reports)."""

    OPEN = "open", _("Open")
    IN_PROGRESS = "in_progress", _("In progress")
    ON_HOLD = "on_hold", _("On hold")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")
    CANCELLED = "cancelled", _("Cancelled")


class Language(models.TextChoices):
    TURKISH = "tr", _("Türkçe")
    ENGLISH = "en", _("English")
    GERMAN = "de", _("Deutsch")
    FRENCH = "fr", _("Français")
    SPANISH = "es", _("Español")
    RUSSIAN = "ru", _("Русский")
    ARABIC = "ar", _("العربية")


# ---------------------------------------------------------------------------
# Helpers used by scoring and validation
# ---------------------------------------------------------------------------
def wind_type_from_directions(wind_direction_deg: float, beach_facing_deg: float) -> str:
    """Classify wind relative to the beach.

    ``beach_facing_deg`` is the compass direction the beach faces (the direction
    you look when standing on the sand facing the water). Wind blowing from the
    land out to sea (offshore) grooms the wave face and is the best condition.
    """
    if wind_direction_deg is None or beach_facing_deg is None:
        return WindType.CROSS_SHORE

    # Meteorological convention: direction the wind comes FROM.
    # Offshore means it comes from the land, i.e. opposite to the beach facing.
    offshore_source = (beach_facing_deg + 180.0) % 360.0
    diff = abs((wind_direction_deg - offshore_source + 180.0) % 360.0 - 180.0)

    if diff <= 30:
        return WindType.OFFSHORE
    if diff <= 60:
        return WindType.CROSS_OFFSHORE
    if diff <= 120:
        return WindType.CROSS_SHORE
    if diff <= 150:
        return WindType.CROSS_ONSHORE
    return WindType.ONSHORE


def recommended_wetsuit(water_temp_c: float | None) -> str:
    """Return the wetsuit recommendation for a water temperature."""
    if water_temp_c is None:
        return "—"
    for low, high, recommendation in WETSUIT_BY_WATER_TEMP:
        if low <= water_temp_c < high:
            return recommendation
    return "6/5 mm + boots + gloves + hood"


def recommended_board_volume(weight_kg: float | None, level: str) -> float | None:
    """Return the recommended board volume in litres for a rider."""
    if not weight_kg:
        return None
    coefficient = BOARD_VOLUME_COEFFICIENT.get(level, 0.7)
    return round(float(weight_kg) * coefficient, 1)
