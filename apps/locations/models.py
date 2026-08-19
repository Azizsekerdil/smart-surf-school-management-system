"""Surf spots and the hazards attached to them.

Design notes
------------
* ``beach_facing_deg`` is mandatory. Without it the school cannot classify wind
  as offshore/onshore, and offshore wind is simultaneously the best condition
  for wave quality and a documented hazard for beginners — so the whole surf
  score and half the safety logic hang off this one number.
* ``is_primary`` is enforced by a partial unique index, not by convention:
  exactly one live spot may be the default. The first spot ever created is
  promoted automatically, and archiving the primary hands the flag to the next
  active spot, so "the default spot" is never undefined.
* A hazard may only apply over part of the tide cycle (a rock that is covered
  at high water, a rip that only runs on the ebb). The tide window is cyclic
  and wraps, e.g. mid-falling → low.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    SURF_LEVEL_ORDER,
    BottomType,
    BreakType,
    Severity,
    SurfLevel,
    TideState,
    WindType,
    level_rank,
    wind_type_from_directions,
)
from apps.core.models import BaseModel, TimeStampedModel
from apps.core.utils import next_sequential_code
from apps.core.validators import (
    phone_validator,
    validate_image_upload,
    validate_latitude,
    validate_longitude,
)

#: The tide cycle in order. Windows wrap around the end of this tuple.
TIDE_CYCLE: tuple[str, ...] = (
    TideState.LOW,
    TideState.MID_RISING,
    TideState.HIGH,
    TideState.MID_FALLING,
)

#: Severity ordered from least to most serious, for sorting and comparisons.
SEVERITY_RANK: dict[str, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

#: 16-point compass, used to render a bearing as something a human can read out.
COMPASS_POINTS: tuple[str, ...] = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

SPOT_CODE_PREFIX = "SPOT"


def compass_label(degrees: float | None) -> str:
    """Return the 16-point compass name for a bearing (``202.5`` -> ``SSW``)."""
    if degrees is None:
        return "—"
    index = int((float(degrees) % 360.0) / 22.5 + 0.5) % 16
    return COMPASS_POINTS[index]


class SurfSpot(BaseModel):
    """A surf break the school operates on."""

    # --- identity ---------------------------------------------------------
    name = models.CharField(_("name"), max_length=120, db_index=True)
    slug = models.SlugField(_("slug"), max_length=140, unique=True, blank=True)
    code = models.CharField(
        _("code"),
        max_length=12,
        unique=True,
        blank=True,
        help_text=_("Short reference used on manifests and radio calls."),
    )
    description = models.TextField(_("description"), blank=True)

    # --- geography --------------------------------------------------------
    latitude = models.FloatField(_("latitude"), validators=[validate_latitude])
    longitude = models.FloatField(_("longitude"), validators=[validate_longitude])
    altitude = models.FloatField(
        _("altitude (m)"),
        null=True,
        blank=True,
        help_text=_("Height of the access point above sea level, in metres."),
    )
    beach_facing_deg = models.FloatField(
        _("beach facing (°)"),
        validators=[MinValueValidator(0.0), MaxValueValidator(360.0)],
        help_text=_(
            "Compass direction you look towards when standing on the sand facing "
            "the water. Required — wind is classified relative to this bearing."
        ),
    )

    # --- the wave ---------------------------------------------------------
    break_type = models.CharField(
        _("break type"),
        max_length=20,
        choices=BreakType.choices,
        default=BreakType.BEACH_BREAK,
        db_index=True,
    )
    bottom_type = models.CharField(
        _("bottom type"),
        max_length=10,
        choices=BottomType.choices,
        default=BottomType.SAND,
        db_index=True,
    )

    # --- who may surf here -----------------------------------------------
    min_level = models.CharField(
        _("minimum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.FIRST_TIME,
        db_index=True,
        help_text=_("The least experienced surfer this break is safe for."),
    )
    max_level = models.CharField(
        _("maximum level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.COMPETITION,
        db_index=True,
        help_text=_("The most advanced level this break still suits."),
    )

    # --- ideal conditions -------------------------------------------------
    ideal_tide = models.CharField(
        _("ideal tide"),
        max_length=15,
        choices=TideState.choices,
        default=TideState.MID_RISING,
    )
    ideal_wind = models.CharField(
        _("ideal wind"),
        max_length=20,
        choices=WindType.choices,
        default=WindType.OFFSHORE,
    )
    ideal_swell_direction_deg = models.FloatField(
        _("ideal swell direction (°)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(360.0)],
        help_text=_("Bearing the best swell arrives from."),
    )

    # --- operations -------------------------------------------------------
    capacity = models.PositiveIntegerField(
        _("capacity"),
        default=20,
        validators=[MinValueValidator(1)],
        help_text=_("Maximum number of students the school puts in the water here at once."),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    is_primary = models.BooleanField(
        _("default spot"),
        default=False,
        help_text=_("The spot used when nothing else is chosen. Only one spot may hold this."),
    )
    parking_info = models.TextField(_("parking"), blank=True)
    access_notes = models.TextField(
        _("access notes"),
        blank=True,
        help_text=_("How to reach the water: path, steps, vehicle access, walking time."),
    )
    photo = models.ImageField(
        _("photo"),
        upload_to="locations/spots/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )

    # --- safety -----------------------------------------------------------
    lifeguard_on_duty = models.BooleanField(
        _("lifeguard service"),
        default=False,
        db_index=True,
        help_text=_("A patrolled beach with a lifeguard service during the season."),
    )
    nearest_hospital = models.CharField(_("nearest hospital"), max_length=200, blank=True)
    nearest_hospital_phone = models.CharField(
        _("hospital phone"), max_length=25, blank=True, validators=[phone_validator]
    )
    emergency_notes = models.TextField(
        _("emergency notes"),
        blank=True,
        help_text=_("Ambulance meeting point, mobile coverage, nearest defibrillator."),
    )

    class Meta:
        verbose_name = _("surf spot")
        verbose_name_plural = _("surf spots")
        ordering = ["-is_primary", "name"]
        indexes = [
            models.Index(fields=["is_active", "is_primary"], name="loc_spot_active_primary"),
            models.Index(fields=["min_level", "max_level"], name="loc_spot_level_range"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_primary"],
                condition=models.Q(is_primary=True, is_deleted=False),
                name="loc_spot_single_primary",
            ),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return self.name

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        errors: dict[str, object] = {}

        if (
            self.min_level
            and self.max_level
            and level_rank(self.min_level) > level_rank(self.max_level)
        ):
            errors["max_level"] = _("The maximum level must not be below the minimum level.")

        if self.beach_facing_deg is not None and not (0.0 <= float(self.beach_facing_deg) <= 360.0):
            errors["beach_facing_deg"] = _("Enter a bearing between 0 and 360 degrees.")

        if self.ideal_swell_direction_deg is not None and not (
            0.0 <= float(self.ideal_swell_direction_deg) <= 360.0
        ):
            errors["ideal_swell_direction_deg"] = _("Enter a bearing between 0 and 360 degrees.")

        if self.capacity is not None and self.capacity < 1:
            errors["capacity"] = _("Capacity must be at least one student.")

        if self.is_primary and not self.is_active:
            errors["is_primary"] = _("An inactive spot cannot be the default spot.")

        if self.nearest_hospital_phone and not self.nearest_hospital:
            errors["nearest_hospital"] = _(
                "Name the hospital the phone number belongs to."
            )

        if errors:
            raise ValidationError(errors)

    # -- persistence -------------------------------------------------------
    def _assign_slug(self) -> None:
        base = slugify(self.name)[:120] or "surf-spot"
        candidate = base
        suffix = 2
        manager = type(self).all_objects
        while manager.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base[:130]}-{suffix}"
            suffix += 1
        self.slug = candidate

    def save(self, *args, **kwargs):
        if not self.slug:
            self._assign_slug()
        if not self.code:
            self.code = next_sequential_code(type(self), "code", SPOT_CODE_PREFIX, width=4)

        manager = type(self).all_objects
        with transaction.atomic():
            if (
                self._state.adding
                and self.is_active
                and not manager.filter(is_primary=True, is_deleted=False).exists()
            ):
                # Never leave "the default spot" undefined: the first live spot
                # of a freshly configured school claims the flag automatically.
                self.is_primary = True
            if self.is_primary:
                manager.filter(is_primary=True).exclude(pk=self.pk).update(is_primary=False)
            return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents: bool = False, hard: bool = False):
        """Soft-delete, handing the default flag to the next active spot."""
        manager = type(self).all_objects
        with transaction.atomic():
            was_primary = self.is_primary
            if was_primary:
                self.is_primary = False
                manager.filter(pk=self.pk).update(is_primary=False)
            result = super().delete(using=using, keep_parents=keep_parents, hard=hard)
            if was_primary:
                successor = (
                    type(self)
                    .objects.filter(is_active=True)
                    .exclude(pk=self.pk)
                    .order_by("name")
                    .first()
                )
                if successor is not None:
                    manager.filter(pk=successor.pk).update(is_primary=True)
        return result

    # -- derived values ----------------------------------------------------
    @property
    def coordinates_display(self) -> str:
        return f"{self.latitude:.5f}, {self.longitude:.5f}"

    @property
    def map_url(self) -> str:
        """Link to this spot on OpenStreetMap (ODbL, no API key, no tracking)."""
        return (
            f"https://www.openstreetmap.org/?mlat={self.latitude}&mlon={self.longitude}"
            f"#map=15/{self.latitude}/{self.longitude}"
        )

    @property
    def directions_url(self) -> str:
        return f"https://www.openstreetmap.org/directions?to={self.latitude}%2C{self.longitude}"

    @property
    def facing_compass(self) -> str:
        return compass_label(self.beach_facing_deg)

    @property
    def offshore_direction_deg(self) -> float:
        """The bearing an offshore wind blows *from* at this spot."""
        return (float(self.beach_facing_deg or 0.0) + 180.0) % 360.0

    @property
    def offshore_compass(self) -> str:
        return compass_label(self.offshore_direction_deg)

    @property
    def swell_compass(self) -> str:
        return compass_label(self.ideal_swell_direction_deg)

    @property
    def level_range_display(self) -> str:
        labels = dict(SurfLevel.choices)
        low = labels.get(self.min_level, self.min_level)
        high = labels.get(self.max_level, self.max_level)
        if self.min_level == self.max_level:
            return str(low)
        return f"{low} – {high}"

    @property
    def suitable_levels(self) -> list[str]:
        """Every :class:`SurfLevel` value this spot accepts."""
        low, high = level_rank(self.min_level), level_rank(self.max_level)
        return [value for value, rank in SURF_LEVEL_ORDER.items() if low <= rank <= high]

    def suits_level(self, level: str) -> bool:
        return level_rank(self.min_level) <= level_rank(level) <= level_rank(self.max_level)

    def classify_wind(self, wind_direction_deg: float | None) -> str:
        """Classify a wind bearing relative to this beach."""
        if wind_direction_deg is None:
            return WindType.GLASSY
        return wind_type_from_directions(float(wind_direction_deg), float(self.beach_facing_deg))

    @property
    def has_emergency_contact(self) -> bool:
        return bool(self.nearest_hospital and self.nearest_hospital_phone)


class SpotHazard(TimeStampedModel):
    """A named danger at a spot — rip, reef shelf, rocks, boat traffic, sewage.

    Hazards are deactivated rather than deleted so the safety history of a break
    stays readable after an incident.
    """

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.CASCADE,
        related_name="hazards",
    )
    name = models.CharField(_("hazard"), max_length=120)
    severity = models.CharField(
        _("severity"),
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    description = models.TextField(_("description"), blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    applies_from_tide = models.CharField(
        _("applies from tide"),
        max_length=15,
        choices=TideState.choices,
        blank=True,
        help_text=_("Leave both tide fields empty if the hazard is present at every tide."),
    )
    applies_to_tide = models.CharField(
        _("applies to tide"), max_length=15, choices=TideState.choices, blank=True
    )

    class Meta:
        verbose_name = _("spot hazard")
        verbose_name_plural = _("spot hazards")
        ordering = ["-is_active", "name"]
        indexes = [
            models.Index(fields=["spot", "is_active"], name="loc_hazard_spot_active"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_severity_display()})"

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        errors: dict[str, object] = {}
        for field in ("applies_from_tide", "applies_to_tide"):
            value = getattr(self, field)
            if value and value not in TIDE_CYCLE:
                errors[field] = _("Choose a point of the tide cycle, not “unknown”.")
        if errors:
            raise ValidationError(errors)

    # -- derived values ----------------------------------------------------
    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    @property
    def is_blocking(self) -> bool:
        """A critical hazard stops the school putting anyone in the water."""
        return self.is_active and self.severity == Severity.CRITICAL

    @property
    def has_tide_window(self) -> bool:
        return bool(self.applies_from_tide or self.applies_to_tide)

    def applies_at_tide(self, tide_state: str | None) -> bool:
        """Is this hazard present at *tide_state*?

        Unknown or unset tide is treated as "yes" — a hazard is never silently
        dismissed because the tide reading is missing.
        """
        if not self.has_tide_window:
            return True
        if not tide_state or tide_state == TideState.UNKNOWN:
            return True

        start = self.applies_from_tide or self.applies_to_tide
        end = self.applies_to_tide or self.applies_from_tide
        if start not in TIDE_CYCLE or end not in TIDE_CYCLE or tide_state not in TIDE_CYCLE:
            return True

        first = TIDE_CYCLE.index(start)
        span = (TIDE_CYCLE.index(end) - first) % len(TIDE_CYCLE)
        offset = (TIDE_CYCLE.index(tide_state) - first) % len(TIDE_CYCLE)
        return offset <= span

    @property
    def tide_window_display(self) -> str:
        if not self.has_tide_window:
            return str(_("All tides"))
        labels = dict(TideState.choices)
        start = labels.get(self.applies_from_tide or self.applies_to_tide, "")
        end = labels.get(self.applies_to_tide or self.applies_from_tide, "")
        if start == end:
            return str(start)
        return f"{start} → {end}"
