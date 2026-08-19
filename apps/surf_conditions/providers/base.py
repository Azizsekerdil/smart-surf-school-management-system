"""The provider interface and the snapshot every provider returns.

Design decisions worth knowing
------------------------------
* **One shape, many sources.** Marine models, weather models and tide models are
  different services with different vocabularies. Everything above this layer
  sees a single :class:`ConditionSnapshot`, so swapping Open-Meteo for met.no
  (or for a paid marine API later) changes one settings value and nothing else.
* **Absence is a value.** Every field defaults to ``None``. met.no has no wave
  model at all; a coastal grid cell may have no sea-surface temperature. Those
  gaps travel intact to the UI, which prints "—". Nothing downstream fabricates
  a substitute, because a made-up wave height would be used to decide whether
  beginners go in the water.
* **Failure is a value too.** ``fetch_current`` returns ``None`` and
  ``fetch_forecast`` returns ``[]`` when the source is unreachable. No provider
  method raises; that is enforced by wrapping every call site in
  :meth:`BaseSurfProvider.request_json`.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

from django.conf import settings

logger = logging.getLogger("apps.surf_conditions")


@dataclass
class ConditionSnapshot:
    """One reading of the ocean and the sky at a spot, at a moment in time.

    ``recorded_at`` is timezone-aware. Everything else is ``None`` when the
    source did not supply it — never zero, never a default.
    """

    recorded_at: datetime | None = None

    # --- the wave ---------------------------------------------------------
    wave_height_m: float | None = None
    wave_period_s: float | None = None
    wave_direction_deg: float | None = None

    # --- the swell that makes it ------------------------------------------
    swell_height_m: float | None = None
    swell_period_s: float | None = None
    swell_direction_deg: float | None = None
    wind_wave_height_m: float | None = None

    # --- the wind ---------------------------------------------------------
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    wind_direction_deg: float | None = None

    # --- the tide ---------------------------------------------------------
    sea_level_height_msl_m: float | None = None
    #: A :class:`apps.core.enums.TideState` value, or ``None``.
    tide_state: str | None = None

    # --- temperature ------------------------------------------------------
    air_temperature_c: float | None = None
    water_temperature_c: float | None = None

    # --- the sky ----------------------------------------------------------
    weather_code: int | None = None
    weather_description: str = ""
    uv_index: float | None = None
    precipitation_mm: float | None = None
    cloud_cover_pct: float | None = None
    visibility_km: float | None = None

    # --- daylight ---------------------------------------------------------
    sunrise: datetime | None = None
    sunset: datetime | None = None

    #: The provider's untouched response fragment, kept for auditing a decision.
    raw: dict = field(default_factory=dict)

    # -- introspection -----------------------------------------------------
    @property
    def has_wave_data(self) -> bool:
        """True when a surf score can be attempted at all."""
        return self.wave_height_m is not None

    @property
    def has_wind_data(self) -> bool:
        return self.wind_speed_kmh is not None or self.wind_direction_deg is not None

    @property
    def is_empty(self) -> bool:
        """True when the snapshot carries no measurement whatsoever."""
        return not any(
            getattr(self, f.name) is not None
            for f in fields(self)
            if f.name not in {"recorded_at", "raw", "weather_description"}
        )

    def measured_fields(self) -> list[str]:
        """Names of the fields the source actually supplied."""
        return [
            f.name
            for f in fields(self)
            if f.name != "raw" and getattr(self, f.name) not in (None, "")
        ]

    def merge(self, other: ConditionSnapshot | None) -> ConditionSnapshot:
        """Fill this snapshot's gaps from *other*, without overwriting anything.

        Used to combine the marine model (waves, sea temperature, sea level)
        with the atmospheric model (wind, air temperature, weather code), which
        Open-Meteo serves from two different endpoints.
        """
        if other is None:
            return self
        for f in fields(self):
            if f.name == "raw":
                continue
            if getattr(self, f.name) in (None, "") and getattr(other, f.name) not in (None, ""):
                setattr(self, f.name, getattr(other, f.name))
        merged_raw = dict(other.raw or {})
        merged_raw.update(self.raw or {})
        self.raw = merged_raw
        return self

    def as_dict(self) -> dict[str, Any]:
        """Plain-Python view, with datetimes as ISO strings (JSON-safe)."""
        payload = asdict(self)
        for key in ("recorded_at", "sunrise", "sunset"):
            value = payload.get(key)
            payload[key] = value.isoformat() if isinstance(value, datetime) else None
        return payload


class BaseSurfProvider(abc.ABC):
    """Interface every surf/weather data source implements."""

    #: Stable identifier used in settings, in the database and in the UI.
    name: str = "base"
    #: Human-readable label.
    label: str = "Base provider"
    #: True when the source refuses to answer without an API key.
    requires_api_key: bool = False
    #: The credit line the licence obliges us to display next to the data.
    attribution: str = ""
    #: True when the source models waves (met.no, for instance, does not).
    provides_marine_data: bool = True

    def __init__(self, config: dict | None = None):
        self.config = config or dict(getattr(settings, "SURF", {}) or {})

    # -- configuration -----------------------------------------------------
    @property
    def timeout_seconds(self) -> int:
        try:
            return int(self.config.get("TIMEOUT_SECONDS", 20))
        except (TypeError, ValueError):
            return 20

    @property
    def cache_seconds(self) -> int:
        try:
            return max(0, int(self.config.get("CACHE_SECONDS", 1800)))
        except (TypeError, ValueError):
            return 1800

    @property
    def is_configured(self) -> bool:
        """True when the provider has everything it needs to be called."""
        return True

    # -- operations --------------------------------------------------------
    @abc.abstractmethod
    def fetch_current(self, spot) -> ConditionSnapshot | None:
        """Conditions right now at *spot*, or ``None`` when unavailable.

        Must never raise.
        """

    @abc.abstractmethod
    def fetch_forecast(self, spot, days: int = 7) -> list[ConditionSnapshot]:
        """Hourly snapshots for the next *days* days, oldest first.

        Returns ``[]`` rather than raising when the source is unreachable.
        """

    @abc.abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Cheap liveness probe: ``(ok, human-readable message)``.

        Must never raise.
        """

    # -- shared HTTP helper ------------------------------------------------
    def request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict | None:
        """GET *url* and return parsed JSON, or ``None`` on any failure.

        Every network call in this package goes through here, which is what
        makes the "providers never raise" promise true rather than aspirational.
        """
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx is a hard requirement
            logger.error("httpx is not installed; %s cannot fetch data.", self.name)
            return None

        try:
            response = httpx.get(
                url,
                params=params or {},
                headers=headers or {},
                timeout=timeout or self.timeout_seconds,
                follow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 - any transport error is just "no data"
            logger.warning("%s: request to %s failed (%s).", self.name, url, type(exc).__name__)
            return None

        if response.status_code != 200:
            # Log the status but never the response body: it can echo the query
            # string, and a paid deployment puts the API key there.
            logger.warning(
                "%s: %s returned HTTP %s.", self.name, url, response.status_code
            )
            return None

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: %s returned non-JSON (%s).", self.name, url, type(exc).__name__)
            return None

        if not isinstance(payload, dict):
            logger.warning("%s: %s returned an unexpected JSON shape.", self.name, url)
            return None
        return payload

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def coerce_float(value) -> float | None:
        """Return *value* as a float, or ``None``. Never raises, never guesses."""
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return number

    @staticmethod
    def coerce_int(value) -> int | None:
        number = BaseSurfProvider.coerce_float(value)
        return None if number is None else int(number)

    def cache_key(self, spot, kind: str) -> str:
        """Cache key for a fetch: provider + rounded position + kind.

        Coordinates are rounded to four decimals (~11 m) so two spots on the
        same beach share one upstream call, which is exactly what the provider's
        grid resolution means anyway.
        """
        latitude = round(float(getattr(spot, "latitude", 0.0) or 0.0), 4)
        longitude = round(float(getattr(spot, "longitude", 0.0) or 0.0), 4)
        return f"surf:{self.name}:{latitude}:{longitude}:{kind}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.__class__.__name__} name={self.name!r}>"
