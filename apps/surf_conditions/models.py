"""What the ocean was doing, and what the school concluded from it.

Three records, three different jobs:

* :class:`SurfCondition` is an **observation**. One reading of one spot at one
  moment, exactly as the provider reported it, plus the untouched payload. It is
  never edited afterwards — if an incident is investigated next winter, this is
  the evidence of what the water looked like.
* :class:`SurfScore` is a **judgement**, one per surf level, derived from that
  observation by arithmetic in :mod:`apps.surf_conditions.services`. It carries
  the factor breakdown so a coach can see *why* the number is what it is, and an
  ``is_ai_generated`` flag that is ``False`` for every score this system
  computes — the flag exists so an AI-written variant could never be mistaken
  for the computed one.
* :class:`ConditionForecast` is a **daily rollup**, cached so the week strip and
  the "best window" answer do not re-derive themselves on every page load.

Nothing here fills a gap with a default. A field the provider did not supply
stays ``NULL`` and renders as "—".
"""

from __future__ import annotations

from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel, TideState, WindType, recommended_wetsuit
from apps.core.models import BaseModel, TimeStampedModel

#: A reading older than this is no longer a description of the present.
STALE_AFTER = timedelta(hours=3)

#: Unit conversions, kept here so every screen and export agrees.
KMH_PER_KNOT = 1.852
METRES_PER_FOOT = 0.3048

#: Score bands used for colour and wording across the UI and the API.
SCORE_BANDS: tuple[tuple[int, str, str], ...] = (
    (80, "excellent", "emerald"),
    (60, "good", "sky"),
    (40, "fair", "amber"),
    (0, "poor", "rose"),
)

#: 16-point compass, mirroring ``apps.locations.models.COMPASS_POINTS`` without
#: importing it — a condition record must not depend on the locations module's
#: internals to render a bearing.
COMPASS_POINTS: tuple[str, ...] = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def compass_label(degrees: float | None) -> str:
    """Render a bearing as a compass point (``202.5`` -> ``SSW``)."""
    if degrees is None:
        return "—"
    try:
        value = float(degrees)
    except (TypeError, ValueError):
        return "—"
    return COMPASS_POINTS[int((value % 360.0) / 22.5 + 0.5) % 16]


def score_band(score: int | None) -> tuple[str, str]:
    """``(band key, colour)`` for a 0-100 score."""
    if score is None:
        return "unknown", "slate"
    for threshold, key, colour in SCORE_BANDS:
        if score >= threshold:
            return key, colour
    return "poor", "rose"


class SurfCondition(BaseModel):
    """One stored reading of the conditions at a spot."""

    class Source(models.TextChoices):
        PROVIDER = "provider", _("Weather provider")
        MANUAL = "manual", _("Entered by staff")
        IMPORT = "import", _("Imported")

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.PROTECT,
        related_name="conditions",
    )
    recorded_at = models.DateTimeField(
        _("recorded at"),
        db_index=True,
        help_text=_("The moment these conditions describe."),
    )
    is_forecast = models.BooleanField(
        _("forecast"),
        default=False,
        db_index=True,
        help_text=_("A modelled future hour rather than an observation of now."),
    )
    source = models.CharField(
        _("source"),
        max_length=20,
        choices=Source.choices,
        default=Source.PROVIDER,
        db_index=True,
    )
    provider = models.CharField(
        _("provider"),
        max_length=40,
        blank=True,
        db_index=True,
        help_text=_("Identifier of the data source, e.g. open-meteo."),
    )

    # --- the wave ---------------------------------------------------------
    wave_height_m = models.FloatField(_("wave height (m)"), null=True, blank=True)
    wave_period_s = models.FloatField(_("wave period (s)"), null=True, blank=True)
    wave_direction_deg = models.FloatField(_("wave direction (°)"), null=True, blank=True)

    # --- the swell --------------------------------------------------------
    swell_height_m = models.FloatField(_("swell height (m)"), null=True, blank=True)
    swell_period_s = models.FloatField(_("swell period (s)"), null=True, blank=True)
    swell_direction_deg = models.FloatField(_("swell direction (°)"), null=True, blank=True)
    wind_wave_height_m = models.FloatField(_("wind wave height (m)"), null=True, blank=True)

    # --- the wind ---------------------------------------------------------
    wind_speed_kmh = models.FloatField(_("wind speed (km/h)"), null=True, blank=True)
    wind_gust_kmh = models.FloatField(_("wind gusts (km/h)"), null=True, blank=True)
    wind_direction_deg = models.FloatField(_("wind direction (°)"), null=True, blank=True)
    wind_type = models.CharField(
        _("wind type"),
        max_length=20,
        choices=WindType.choices,
        blank=True,
        db_index=True,
        help_text=_("Classified against the spot's beach orientation."),
    )

    # --- the tide ---------------------------------------------------------
    sea_level_height_msl_m = models.FloatField(
        _("sea level above MSL (m)"), null=True, blank=True
    )
    tide_state = models.CharField(
        _("tide"),
        max_length=15,
        choices=TideState.choices,
        default=TideState.UNKNOWN,
        db_index=True,
        help_text=_("Derived from the modelled sea level — not a tide-station reading."),
    )

    # --- temperature ------------------------------------------------------
    air_temperature_c = models.FloatField(_("air temperature (°C)"), null=True, blank=True)
    water_temperature_c = models.FloatField(_("water temperature (°C)"), null=True, blank=True)

    # --- the sky ----------------------------------------------------------
    weather_code = models.IntegerField(
        _("weather code"),
        null=True,
        blank=True,
        help_text=_("WMO 4677 present-weather code."),
    )
    weather_description = models.CharField(_("weather"), max_length=120, blank=True)
    uv_index = models.FloatField(_("UV index"), null=True, blank=True)
    precipitation_mm = models.FloatField(_("precipitation (mm)"), null=True, blank=True)
    cloud_cover_pct = models.FloatField(_("cloud cover (%)"), null=True, blank=True)
    visibility_km = models.FloatField(_("visibility (km)"), null=True, blank=True)

    # --- daylight ---------------------------------------------------------
    sunrise = models.DateTimeField(_("sunrise"), null=True, blank=True)
    sunset = models.DateTimeField(_("sunset"), null=True, blank=True)

    raw_payload = models.JSONField(
        _("provider payload"),
        default=dict,
        blank=True,
        help_text=_("The provider's response, kept so a past decision can be audited."),
    )

    class Meta:
        verbose_name = _("surf condition")
        verbose_name_plural = _("surf conditions")
        ordering = ["-recorded_at", "spot__name"]
        indexes = [
            models.Index(fields=["spot", "-recorded_at"], name="sc_cond_spot_recorded"),
            models.Index(fields=["spot", "is_forecast", "recorded_at"], name="sc_cond_spot_fc"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["spot", "recorded_at", "is_forecast"],
                name="sc_cond_unique_reading",
            ),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        label = _("Forecast") if self.is_forecast else _("Observed")
        moment = timezone.localtime(self.recorded_at) if self.recorded_at else None
        stamp = moment.strftime("%d.%m.%Y %H:%M") if moment else "—"
        spot_name = self.spot.name if self.spot_id else "—"
        return f"{spot_name} · {label} · {stamp}"

    # -- derived values ----------------------------------------------------
    @property
    def wind_knots(self) -> float | None:
        """Wind speed in knots — the unit marine forecasts are read in."""
        if self.wind_speed_kmh is None:
            return None
        return round(self.wind_speed_kmh / KMH_PER_KNOT, 1)

    @property
    def gust_knots(self) -> float | None:
        if self.wind_gust_kmh is None:
            return None
        return round(self.wind_gust_kmh / KMH_PER_KNOT, 1)

    @property
    def wave_height_ft(self) -> float | None:
        """Wave height in feet, for the half of the surf world that uses them."""
        if self.wave_height_m is None:
            return None
        return round(self.wave_height_m / METRES_PER_FOOT, 1)

    @property
    def swell_height_ft(self) -> float | None:
        if self.swell_height_m is None:
            return None
        return round(self.swell_height_m / METRES_PER_FOOT, 1)

    @property
    def is_stale(self) -> bool:
        """True when this observation is older than three hours.

        Conditions turn inside an hour. A stale reading is shown with a warning
        rather than hidden, because "we last saw this at 06:00" is information
        and a blank panel is not.
        """
        if self.is_forecast or self.recorded_at is None:
            return False
        return timezone.now() - self.recorded_at > STALE_AFTER

    @property
    def age_minutes(self) -> int | None:
        if self.recorded_at is None:
            return None
        return int((timezone.now() - self.recorded_at).total_seconds() // 60)

    @property
    def recommended_wetsuit(self) -> str:
        """Wetsuit thickness for this water temperature."""
        return recommended_wetsuit(self.water_temperature_c)

    @property
    def effective_period_s(self) -> float | None:
        """The period to judge wave quality by: swell period, else wave period."""
        return self.swell_period_s if self.swell_period_s is not None else self.wave_period_s

    @property
    def wind_compass(self) -> str:
        return compass_label(self.wind_direction_deg)

    @property
    def swell_compass(self) -> str:
        return compass_label(self.swell_direction_deg)

    @property
    def wave_compass(self) -> str:
        return compass_label(self.wave_direction_deg)

    @property
    def has_wave_data(self) -> bool:
        """False when the source has no marine model (met.no, for example)."""
        return self.wave_height_m is not None

    @property
    def weather_icon(self) -> str:
        from .providers.open_meteo import weather_icon_name

        return weather_icon_name(self.weather_code)

    @property
    def has_lightning(self) -> bool:
        from .providers.open_meteo import is_thunderstorm

        return is_thunderstorm(self.weather_code)

    def score_for(self, level: str):
        """The stored :class:`SurfScore` for *level*, or ``None``."""
        cached = getattr(self, "_prefetched_objects_cache", {}).get("scores")
        if cached is not None:
            return next((score for score in cached if score.level == level), None)
        return self.scores.filter(level=level).first()


class SurfScore(TimeStampedModel):
    """How suitable one reading is for one surf level.

    ``factors`` holds the component breakdown produced by
    :func:`apps.surf_conditions.services.calculate_surf_score` — a list of
    ``{name, value, score, weight, note}`` dicts. The UI expands it so the number
    is never a black box a coach has to trust blindly.
    """

    condition = models.ForeignKey(
        SurfCondition,
        verbose_name=_("condition"),
        on_delete=models.CASCADE,
        related_name="scores",
    )
    level = models.CharField(
        _("surf level"), max_length=20, choices=SurfLevel.choices, db_index=True
    )
    score = models.PositiveSmallIntegerField(
        _("score"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("0-100. Computed from measured values, not generated by AI."),
    )
    factors = models.JSONField(
        _("factors"),
        default=list,
        blank=True,
        help_text=_("The weighted components behind the score, for the explanation panel."),
    )
    recommendation = models.TextField(_("recommendation"), blank=True)
    is_safe_for_level = models.BooleanField(
        _("safe for this level"),
        default=True,
        db_index=True,
        help_text=_("False when wave height or wind exceeds the hard limit for this level."),
    )
    is_ai_generated = models.BooleanField(
        _("AI generated"),
        default=False,
        help_text=_("Always False for the computed score. AI never sets the number."),
    )

    class Meta:
        verbose_name = _("surf score")
        verbose_name_plural = _("surf scores")
        ordering = ["condition", "level"]
        indexes = [
            models.Index(fields=["level", "-score"], name="sc_score_level_value"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["condition", "level"], name="sc_score_unique_level"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_level_display()}: {self.score}/100"

    @property
    def band(self) -> str:
        return score_band(self.score)[0]

    @property
    def band_color(self) -> str:
        """Tailwind colour family used by the badge and the gauge."""
        return score_band(self.score)[1]

    @property
    def band_label(self) -> str:
        labels = {
            "excellent": _("Excellent"),
            "good": _("Good"),
            "fair": _("Fair"),
            "poor": _("Poor"),
            "unknown": _("Unknown"),
        }
        return str(labels.get(self.band, labels["unknown"]))

    @property
    def blocking_factors(self) -> list:
        """Factors that dragged the score down hardest — the "why" in one line."""
        items = [f for f in (self.factors or []) if isinstance(f, dict)]
        scored = [f for f in items if isinstance(f.get("score"), (int, float))]
        return sorted(scored, key=lambda f: f["score"])[:2]


class ConditionForecast(BaseModel):
    """A cached daily rollup of the hourly forecast for one spot and one day."""

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.PROTECT,
        related_name="daily_forecasts",
    )
    date = models.DateField(_("date"), db_index=True)
    generated_at = models.DateTimeField(_("generated at"), default=timezone.now)
    summary = models.JSONField(
        _("summary"),
        default=dict,
        blank=True,
        help_text=_("The hourly series and the day's aggregates, ready for the chart."),
    )
    best_window_start = models.TimeField(_("best window from"), null=True, blank=True)
    best_window_end = models.TimeField(_("best window to"), null=True, blank=True)
    best_level = models.CharField(
        _("best suited level"),
        max_length=20,
        choices=SurfLevel.choices,
        blank=True,
        help_text=_("The level this day scores highest for. Blank when no wave data exists."),
    )

    class Meta:
        verbose_name = _("condition forecast")
        verbose_name_plural = _("condition forecasts")
        ordering = ["spot__name", "date"]
        indexes = [
            models.Index(fields=["spot", "date"], name="sc_fc_spot_date"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["spot", "date"], name="sc_fc_unique_spot_day"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        spot_name = self.spot.name if self.spot_id else "—"
        return f"{spot_name} · {self.date:%d.%m.%Y}"

    # -- derived values ----------------------------------------------------
    @property
    def has_best_window(self) -> bool:
        return bool(self.best_window_start and self.best_window_end)

    @property
    def best_window_display(self) -> str:
        if not self.has_best_window:
            return "—"
        return f"{self.best_window_start:%H:%M} – {self.best_window_end:%H:%M}"

    @property
    def wave_height_max(self) -> float | None:
        value = (self.summary or {}).get("wave_height_max")
        return value if isinstance(value, (int, float)) else None

    @property
    def wave_height_min(self) -> float | None:
        value = (self.summary or {}).get("wave_height_min")
        return value if isinstance(value, (int, float)) else None

    @property
    def wind_speed_max(self) -> float | None:
        value = (self.summary or {}).get("wind_speed_max")
        return value if isinstance(value, (int, float)) else None

    @property
    def best_score(self) -> int | None:
        value = (self.summary or {}).get("best_score")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def weather_code(self) -> int | None:
        value = (self.summary or {}).get("weather_code")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def weather_icon(self) -> str:
        from .providers.open_meteo import weather_icon_name

        return weather_icon_name(self.weather_code)

    @property
    def weather_description(self) -> str:
        from .providers.open_meteo import describe_weather_code

        return describe_weather_code(self.weather_code)

    @property
    def hours(self) -> list:
        value = (self.summary or {}).get("hours")
        return value if isinstance(value, list) else []
