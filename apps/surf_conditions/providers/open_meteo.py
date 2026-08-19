"""Open-Meteo — the default source for waves, wind, weather and modelled tide.

Why this one
------------
It needs no API key, it publishes a marine model *and* an atmospheric model on
the same grid, and the data is CC BY 4.0. The credit line in :attr:`attribution`
is a licence obligation, not decoration, and it is rendered on every screen that
shows this data.

Licence caveat: the CC BY 4.0 terms cover the *data*. The free hosted service is
for non-commercial use. ``settings.SURF["COMMERCIAL_MODE"]`` therefore pushes the
registry towards met.no unless a paid Open-Meteo key is configured — see
:mod:`apps.surf_conditions.providers.registry`.

Endpoint details that are easy to get wrong
-------------------------------------------
* The marine API needs ``cell_selection=sea``. Without it a coastal point can
  land in a land cell and every wave field comes back ``null``.
* The swell parameters are prefixed ``swell_wave_`` — ``swell_wave_height``, not
  ``swell_height``. The short spelling is rejected with HTTP 400.
* ``sunrise``/``sunset`` exist only in the ``daily`` block.
* ``timeformat=unixtime`` is requested deliberately: the ISO strings Open-Meteo
  returns are *naive local* times, and this project runs with ``USE_TZ=True``.
  Parsing them would silently produce wrong instants twice a year.

Tide
----
Open-Meteo models ``sea_level_height_msl``; it is not a tide-gauge reading. The
tide state is therefore derived from the shape of the surrounding hourly series
and is labelled "modelled tide" everywhere it appears.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import date as date_cls

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from apps.core.enums import TideState

from .base import BaseSurfProvider, ConditionSnapshot

logger = logging.getLogger("apps.surf_conditions")

#: Shown wherever a tide state derived from the sea-level model is displayed.
TIDE_SOURCE_LABEL = _("Modelled tide — derived from the sea-level model, not a tide station")

#: Below this range (metres) over a tide cycle the model carries no usable tidal
#: signal; the Mediterranean is the obvious example. Better "unknown" than a
#: confident reading of noise.
MIN_TIDAL_RANGE_M = 0.05

#: How many hours either side of a moment are inspected to place it in the tide
#: cycle. A semi-diurnal cycle is ~12.4 h, so ±6 h always contains a turn.
TIDE_WINDOW_HOURS = 6


# ---------------------------------------------------------------------------
# WMO 4677 present-weather codes
# ---------------------------------------------------------------------------
#: The full 00–99 table. Codes Open-Meteo actually emits use Open-Meteo's own
#: wording (that is what the number *means* in this API); the remainder use the
#: WMO 4677 definition, so a code from any other source still resolves to text.
WMO_WEATHER_CODES: dict[int, object] = {
    0: _("Clear sky"),
    1: _("Mainly clear"),
    2: _("Partly cloudy"),
    3: _("Overcast"),
    4: _("Visibility reduced by smoke"),
    5: _("Haze"),
    6: _("Widespread dust in suspension"),
    7: _("Dust or sand raised by wind"),
    8: _("Well-developed dust or sand whirls"),
    9: _("Duststorm or sandstorm within sight"),
    10: _("Mist"),
    11: _("Patches of shallow fog"),
    12: _("Continuous shallow fog"),
    13: _("Lightning visible, no thunder heard"),
    14: _("Precipitation in sight, not reaching the ground"),
    15: _("Precipitation in sight, reaching the ground, distant"),
    16: _("Precipitation in sight, reaching the ground, nearby"),
    17: _("Thunderstorm, no precipitation at present"),
    18: _("Squalls"),
    19: _("Funnel cloud"),
    20: _("Drizzle or snow grains during the past hour"),
    21: _("Rain during the past hour"),
    22: _("Snow during the past hour"),
    23: _("Rain and snow, or ice pellets, during the past hour"),
    24: _("Freezing drizzle or freezing rain during the past hour"),
    25: _("Rain showers during the past hour"),
    26: _("Snow showers during the past hour"),
    27: _("Hail showers during the past hour"),
    28: _("Fog during the past hour"),
    29: _("Thunderstorm during the past hour"),
    30: _("Slight or moderate duststorm, decreasing"),
    31: _("Slight or moderate duststorm, no change"),
    32: _("Slight or moderate duststorm, increasing"),
    33: _("Severe duststorm, decreasing"),
    34: _("Severe duststorm, no change"),
    35: _("Severe duststorm, increasing"),
    36: _("Slight or moderate drifting snow, low"),
    37: _("Heavy drifting snow, low"),
    38: _("Slight or moderate blowing snow, high"),
    39: _("Heavy blowing snow, high"),
    40: _("Fog at a distance"),
    41: _("Fog in patches"),
    42: _("Fog thinning, sky visible"),
    43: _("Fog thinning, sky obscured"),
    44: _("Fog, no change, sky visible"),
    45: _("Fog"),
    46: _("Fog thickening, sky visible"),
    47: _("Fog thickening, sky obscured"),
    48: _("Depositing rime fog"),
    49: _("Depositing rime fog, sky obscured"),
    50: _("Intermittent light drizzle"),
    51: _("Light drizzle"),
    52: _("Intermittent moderate drizzle"),
    53: _("Moderate drizzle"),
    54: _("Intermittent heavy drizzle"),
    55: _("Dense drizzle"),
    56: _("Light freezing drizzle"),
    57: _("Dense freezing drizzle"),
    58: _("Light drizzle and rain"),
    59: _("Moderate or heavy drizzle and rain"),
    60: _("Intermittent slight rain"),
    61: _("Slight rain"),
    62: _("Intermittent moderate rain"),
    63: _("Moderate rain"),
    64: _("Intermittent heavy rain"),
    65: _("Heavy rain"),
    66: _("Light freezing rain"),
    67: _("Heavy freezing rain"),
    68: _("Slight rain or drizzle with snow"),
    69: _("Moderate or heavy rain or drizzle with snow"),
    70: _("Intermittent slight snowfall"),
    71: _("Slight snowfall"),
    72: _("Intermittent moderate snowfall"),
    73: _("Moderate snowfall"),
    74: _("Intermittent heavy snowfall"),
    75: _("Heavy snowfall"),
    76: _("Diamond dust"),
    77: _("Snow grains"),
    78: _("Isolated snow crystals"),
    79: _("Ice pellets"),
    80: _("Slight rain showers"),
    81: _("Moderate rain showers"),
    82: _("Violent rain showers"),
    83: _("Slight showers of rain and snow"),
    84: _("Moderate or heavy showers of rain and snow"),
    85: _("Slight snow showers"),
    86: _("Heavy snow showers"),
    87: _("Slight showers of snow pellets or small hail"),
    88: _("Moderate or heavy showers of snow pellets or small hail"),
    89: _("Slight hail showers"),
    90: _("Moderate or heavy hail showers"),
    91: _("Slight rain, thunderstorm in the past hour"),
    92: _("Moderate or heavy rain, thunderstorm in the past hour"),
    93: _("Slight snow or hail, thunderstorm in the past hour"),
    94: _("Moderate or heavy snow or hail, thunderstorm in the past hour"),
    95: _("Thunderstorm"),
    96: _("Thunderstorm with slight hail"),
    97: _("Heavy thunderstorm"),
    98: _("Thunderstorm with duststorm or sandstorm"),
    99: _("Thunderstorm with heavy hail"),
}

#: Codes that mean lightning is in play. Nobody goes in the water on these.
THUNDERSTORM_CODES: frozenset[int] = frozenset({17, 29, 91, 92, 93, 94, 95, 96, 97, 98, 99})

#: Codes that mean visibility is compromised — a rescue problem, not a comfort one.
FOG_CODES: frozenset[int] = frozenset(range(40, 50)) | {10, 11, 12, 28}


def describe_weather_code(code: int | None) -> str:
    """Human text for a WMO code (empty string when the code is unknown)."""
    if code is None:
        return ""
    try:
        key = int(code)
    except (TypeError, ValueError):
        return ""
    label = WMO_WEATHER_CODES.get(key)
    return str(label) if label is not None else ""


def weather_icon_name(code: int | None) -> str:
    """Vendored Lucide icon name for a WMO code."""
    if code is None:
        return "cloud"
    try:
        key = int(code)
    except (TypeError, ValueError):
        return "cloud"
    if key in THUNDERSTORM_CODES:
        return "zap"
    if key in (0, 1):
        return "sun"
    if 50 <= key <= 69 or 80 <= key <= 84:
        return "cloud-rain"
    if key in FOG_CODES:
        return "cloud"
    return "cloud"


def is_thunderstorm(code: int | None) -> bool:
    try:
        return int(code) in THUNDERSTORM_CODES
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Tide derivation
# ---------------------------------------------------------------------------
def derive_tide_state(levels: Sequence[float | None], index: int) -> str:
    """Place ``levels[index]`` in the tide cycle.

    The model gives a sea-level height per hour, not a tide table. Reading the
    surrounding series tells us two things a single number cannot: where this
    height sits between the local low and the local high, and whether the water
    is on its way up or down.

    Returns a :class:`~apps.core.enums.TideState` value; ``UNKNOWN`` whenever
    the series is too short or too flat to support a claim.
    """
    if not levels or index < 0 or index >= len(levels):
        return TideState.UNKNOWN

    current = levels[index]
    if current is None:
        return TideState.UNKNOWN

    start = max(0, index - TIDE_WINDOW_HOURS)
    end = min(len(levels), index + TIDE_WINDOW_HOURS + 1)
    window = [value for value in levels[start:end] if value is not None]
    if len(window) < 3:
        return TideState.UNKNOWN

    low, high = min(window), max(window)
    span = high - low
    if span < MIN_TIDAL_RANGE_M:
        # A near-tideless sea (the Mediterranean, for instance). Saying
        # "unknown" is honest; saying "mid-rising" would be noise dressed up.
        return TideState.UNKNOWN

    position = (current - low) / span

    previous_value = next(
        (levels[i] for i in range(index - 1, start - 1, -1) if levels[i] is not None), None
    )
    next_value = next(
        (levels[i] for i in range(index + 1, end) if levels[i] is not None), None
    )

    if previous_value is not None and next_value is not None:
        slope = next_value - previous_value
    elif next_value is not None:
        slope = next_value - current
    elif previous_value is not None:
        slope = current - previous_value
    else:
        slope = 0.0

    if position >= 0.8:
        return TideState.HIGH
    if position <= 0.2:
        return TideState.LOW
    if slope > 0:
        return TideState.MID_RISING
    if slope < 0:
        return TideState.MID_FALLING
    return TideState.HIGH if position > 0.5 else TideState.LOW


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _instant_from_epoch(value) -> datetime | None:
    """UTC-aware datetime from a UNIX timestamp, or ``None``."""
    seconds = BaseSurfProvider.coerce_float(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _local_date_from_epoch(value, utc_offset_seconds: int) -> date_cls | None:
    """Local calendar date of a daily bucket.

    Open-Meteo documents that daily timestamps need ``utc_offset_seconds``
    applied again before the date is read — otherwise a spot east of Greenwich
    reports every forecast one day early after 21:00 UTC.
    """
    seconds = BaseSurfProvider.coerce_float(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds + utc_offset_seconds, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


class OpenMeteoProvider(BaseSurfProvider):
    """Waves and swell from the marine model, wind and sky from the forecast model."""

    name = "open-meteo"
    label = "Open-Meteo"
    requires_api_key = False
    attribution = "Weather data by Open-Meteo.com (CC BY 4.0)"
    provides_marine_data = True

    #: Marine endpoint parameter lists. ``swell_wave_`` prefix is mandatory.
    MARINE_CURRENT = (
        "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,"
        "swell_wave_period,wind_wave_height,sea_surface_temperature,sea_level_height_msl"
    )
    MARINE_HOURLY = (
        "wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_period,"
        "swell_wave_direction,sea_surface_temperature,sea_level_height_msl"
    )
    MARINE_DAILY = (
        "wave_height_max,wave_direction_dominant,wave_period_max,swell_wave_height_max"
    )

    #: Atmospheric endpoint parameter lists. sunrise/sunset are daily-only.
    FORECAST_CURRENT = (
        "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
        "cloud_cover,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index"
    )
    FORECAST_HOURLY = (
        "temperature_2m,precipitation_probability,weather_code,wind_speed_10m,"
        "wind_direction_10m,uv_index,visibility"
    )
    FORECAST_DAILY = (
        "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,"
        "precipitation_sum,wind_speed_10m_max,wind_direction_10m_dominant"
    )

    #: One upstream call serves both "now" and the week, so ask for the week.
    DEFAULT_FORECAST_DAYS = 7
    MAX_FORECAST_DAYS = 16

    # -- configuration -----------------------------------------------------
    @property
    def api_key(self) -> str:
        return (self.config.get("OPEN_METEO_API_KEY") or "").strip()

    @property
    def marine_url(self) -> str:
        url = self.config.get("OPEN_METEO_MARINE_URL") or (
            "https://marine-api.open-meteo.com/v1/marine"
        )
        if self.api_key:
            # A paid subscription is served from the customer host.
            url = url.replace("://marine-api.", "://customer-marine-api.")
        return url

    @property
    def forecast_url(self) -> str:
        url = self.config.get("OPEN_METEO_FORECAST_URL") or (
            "https://api.open-meteo.com/v1/forecast"
        )
        if self.api_key:
            url = url.replace("://api.", "://customer-api.")
        return url

    def _common_params(self, spot, days: int) -> dict:
        params: dict[str, object] = {
            "latitude": float(spot.latitude),
            "longitude": float(spot.longitude),
            "timezone": "auto",
            "timeformat": "unixtime",
            "forecast_days": max(1, min(int(days), self.MAX_FORECAST_DAYS)),
        }
        if self.api_key:
            params["apikey"] = self.api_key
        return params

    # -- fetching ----------------------------------------------------------
    def _payload(self, spot, kind: str, days: int) -> dict | None:
        """Fetch (or reuse a cached copy of) one endpoint's whole response.

        The cache is keyed on provider + position + kind, so every spot on the
        same beach and every screen in the app share one upstream request for
        ``settings.SURF["CACHE_SECONDS"]``. This is what keeps the school inside
        a free tier while ten staff refresh the dashboard.
        """
        key = f"{self.cache_key(spot, kind)}:{days}"
        cached = cache.get(key)
        if cached is not None:
            return cached

        params = self._common_params(spot, days)
        if kind == "marine":
            params.update(
                {
                    # Without cell_selection=sea a beach can resolve to a land
                    # cell and every wave field returns null.
                    "cell_selection": "sea",
                    "current": self.MARINE_CURRENT,
                    "hourly": self.MARINE_HOURLY,
                    "daily": self.MARINE_DAILY,
                }
            )
            url = self.marine_url
        else:
            params.update(
                {
                    "current": self.FORECAST_CURRENT,
                    "hourly": self.FORECAST_HOURLY,
                    "daily": self.FORECAST_DAILY,
                }
            )
            url = self.forecast_url

        payload = self.request_json(url, params)
        if payload is None:
            return None
        if payload.get("error"):
            logger.warning(
                "%s: %s endpoint reported an error for %s.", self.name, kind, spot
            )
            return None
        if self.cache_seconds:
            cache.set(key, payload, self.cache_seconds)
        return payload

    # -- parsing helpers ---------------------------------------------------
    @staticmethod
    def _offset(payload: dict | None) -> int:
        if not payload:
            return 0
        value = BaseSurfProvider.coerce_int(payload.get("utc_offset_seconds"))
        return value or 0

    @staticmethod
    def _series(payload: dict | None, block: str, key: str) -> list:
        if not payload:
            return []
        values = (payload.get(block) or {}).get(key)
        return list(values) if isinstance(values, list) else []

    @staticmethod
    def _nearest_index(times: Sequence, moment: datetime) -> int | None:
        """Index of the hourly slot closest to *moment*."""
        best_index, best_gap = None, None
        target = moment.timestamp()
        for index, value in enumerate(times):
            seconds = BaseSurfProvider.coerce_float(value)
            if seconds is None:
                continue
            gap = abs(seconds - target)
            if best_gap is None or gap < best_gap:
                best_index, best_gap = index, gap
        return best_index

    def _daylight_by_date(self, weather: dict | None) -> dict[date_cls, tuple[datetime | None, datetime | None]]:
        """``{local date: (sunrise, sunset)}`` from the daily block."""
        if not weather:
            return {}
        offset = self._offset(weather)
        days = self._series(weather, "daily", "time")
        sunrises = self._series(weather, "daily", "sunrise")
        sunsets = self._series(weather, "daily", "sunset")

        result: dict[date_cls, tuple[datetime | None, datetime | None]] = {}
        for index, day_value in enumerate(days):
            day = _local_date_from_epoch(day_value, offset)
            if day is None:
                continue
            sunrise = _instant_from_epoch(sunrises[index]) if index < len(sunrises) else None
            sunset = _instant_from_epoch(sunsets[index]) if index < len(sunsets) else None
            # Sanity gate: a sunrise after its sunset, or a "day" longer than a
            # day, means the payload is not what we assumed. Drop it rather than
            # print a wrong time on a safety screen.
            if sunrise and sunset:
                span = (sunset - sunrise).total_seconds()
                if span <= 0 or span > 86400:
                    sunrise = sunset = None
            result[day] = (sunrise, sunset)
        return result

    @staticmethod
    def _at(series: Sequence, index: int):
        if index is None or index < 0 or index >= len(series):
            return None
        return series[index]

    # -- the interface -----------------------------------------------------
    def fetch_current(self, spot) -> ConditionSnapshot | None:
        """Conditions now: marine ``current`` merged with weather ``current``."""
        try:
            marine = self._payload(spot, "marine", self.DEFAULT_FORECAST_DAYS)
            weather = self._payload(spot, "weather", self.DEFAULT_FORECAST_DAYS)
            if marine is None and weather is None:
                return None

            now = datetime.now(tz=UTC)
            marine_current = (marine or {}).get("current") or {}
            weather_current = (weather or {}).get("current") or {}

            recorded_at = (
                _instant_from_epoch(weather_current.get("time"))
                or _instant_from_epoch(marine_current.get("time"))
                or now
            )

            floats = self.coerce_float
            visibility_m = floats(weather_current.get("visibility"))
            weather_code = self.coerce_int(weather_current.get("weather_code"))

            snapshot = ConditionSnapshot(
                recorded_at=recorded_at,
                wave_height_m=floats(marine_current.get("wave_height")),
                wave_period_s=floats(marine_current.get("wave_period")),
                wave_direction_deg=floats(marine_current.get("wave_direction")),
                swell_height_m=floats(marine_current.get("swell_wave_height")),
                swell_period_s=floats(marine_current.get("swell_wave_period")),
                swell_direction_deg=floats(marine_current.get("swell_wave_direction")),
                wind_wave_height_m=floats(marine_current.get("wind_wave_height")),
                wind_speed_kmh=floats(weather_current.get("wind_speed_10m")),
                wind_gust_kmh=floats(weather_current.get("wind_gusts_10m")),
                wind_direction_deg=floats(weather_current.get("wind_direction_10m")),
                sea_level_height_msl_m=floats(marine_current.get("sea_level_height_msl")),
                air_temperature_c=floats(weather_current.get("temperature_2m")),
                water_temperature_c=floats(marine_current.get("sea_surface_temperature")),
                weather_code=weather_code,
                weather_description=describe_weather_code(weather_code),
                uv_index=floats(weather_current.get("uv_index")),
                precipitation_mm=floats(weather_current.get("precipitation")),
                cloud_cover_pct=floats(weather_current.get("cloud_cover")),
                visibility_km=None if visibility_m is None else round(visibility_m / 1000.0, 2),
                raw={
                    "provider": self.name,
                    "marine_current": marine_current,
                    "weather_current": weather_current,
                    "marine_units": (marine or {}).get("current_units") or {},
                    "weather_units": (weather or {}).get("current_units") or {},
                    "elevation": (marine or weather or {}).get("elevation"),
                },
            )

            # Tide: read the modelled sea-level series around this moment.
            hourly_times = self._series(marine, "hourly", "time")
            levels = [
                floats(value) for value in self._series(marine, "hourly", "sea_level_height_msl")
            ]
            index = self._nearest_index(hourly_times, recorded_at)
            if index is not None and levels:
                if snapshot.sea_level_height_msl_m is None:
                    snapshot.sea_level_height_msl_m = self._at(levels, index)
                snapshot.tide_state = derive_tide_state(levels, index)
            else:
                snapshot.tide_state = TideState.UNKNOWN

            daylight = self._daylight_by_date(weather)
            offset = self._offset(weather)
            local_day = (
                datetime.fromtimestamp(recorded_at.timestamp() + offset, tz=UTC).date()
            )
            sunrise, sunset = daylight.get(local_day, (None, None))
            snapshot.sunrise, snapshot.sunset = sunrise, sunset

            if snapshot.is_empty:
                logger.info("%s returned no usable values for %s.", self.name, spot)
                return None
            return snapshot
        except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
            logger.warning("%s: fetch_current failed for %s (%s).", self.name, spot, exc)
            return None

    def fetch_forecast(self, spot, days: int = 7) -> list[ConditionSnapshot]:
        """Hourly snapshots for the next *days* days, oldest first."""
        try:
            days = max(1, min(int(days or 1), self.MAX_FORECAST_DAYS))
            marine = self._payload(spot, "marine", days)
            weather = self._payload(spot, "weather", days)
            if marine is None and weather is None:
                return []

            floats = self.coerce_float
            marine_times = self._series(marine, "hourly", "time")
            weather_times = self._series(weather, "hourly", "time")
            # The marine series drives the loop when it exists — the wave fields
            # are the reason this screen exists. Without it the atmospheric
            # series still produces wind-only rows.
            times = marine_times or weather_times
            if not times:
                return []

            wave_heights = [floats(v) for v in self._series(marine, "hourly", "wave_height")]
            wave_periods = [floats(v) for v in self._series(marine, "hourly", "wave_period")]
            wave_dirs = [floats(v) for v in self._series(marine, "hourly", "wave_direction")]
            swell_heights = [
                floats(v) for v in self._series(marine, "hourly", "swell_wave_height")
            ]
            swell_periods = [
                floats(v) for v in self._series(marine, "hourly", "swell_wave_period")
            ]
            swell_dirs = [
                floats(v) for v in self._series(marine, "hourly", "swell_wave_direction")
            ]
            sea_temps = [
                floats(v) for v in self._series(marine, "hourly", "sea_surface_temperature")
            ]
            levels = [
                floats(v) for v in self._series(marine, "hourly", "sea_level_height_msl")
            ]

            air_temps = [floats(v) for v in self._series(weather, "hourly", "temperature_2m")]
            precip_prob = [
                floats(v) for v in self._series(weather, "hourly", "precipitation_probability")
            ]
            codes = [self.coerce_int(v) for v in self._series(weather, "hourly", "weather_code")]
            wind_speeds = [floats(v) for v in self._series(weather, "hourly", "wind_speed_10m")]
            wind_dirs = [floats(v) for v in self._series(weather, "hourly", "wind_direction_10m")]
            uv = [floats(v) for v in self._series(weather, "hourly", "uv_index")]
            visibility = [floats(v) for v in self._series(weather, "hourly", "visibility")]

            # The two endpoints share a grid and a timezone, so their hourly
            # arrays line up index-for-index. Verified rather than assumed: when
            # the timestamps disagree the weather series is looked up by time.
            weather_index_by_time: dict[float, int] = {}
            for index, value in enumerate(weather_times):
                seconds = floats(value)
                if seconds is not None:
                    weather_index_by_time[seconds] = index

            daylight = self._daylight_by_date(weather)
            offset = self._offset(weather) or self._offset(marine)

            snapshots: list[ConditionSnapshot] = []
            for index, time_value in enumerate(times):
                moment = _instant_from_epoch(time_value)
                if moment is None:
                    continue
                seconds = floats(time_value)
                w_index = weather_index_by_time.get(seconds, index)

                code = self._at(codes, w_index)
                visibility_m = self._at(visibility, w_index)
                local_day = datetime.fromtimestamp(
                    moment.timestamp() + offset, tz=UTC
                ).date()
                sunrise, sunset = daylight.get(local_day, (None, None))

                snapshot = ConditionSnapshot(
                    recorded_at=moment,
                    wave_height_m=self._at(wave_heights, index),
                    wave_period_s=self._at(wave_periods, index),
                    wave_direction_deg=self._at(wave_dirs, index),
                    swell_height_m=self._at(swell_heights, index),
                    swell_period_s=self._at(swell_periods, index),
                    swell_direction_deg=self._at(swell_dirs, index),
                    wind_speed_kmh=self._at(wind_speeds, w_index),
                    wind_direction_deg=self._at(wind_dirs, w_index),
                    sea_level_height_msl_m=self._at(levels, index),
                    tide_state=derive_tide_state(levels, index) if levels else TideState.UNKNOWN,
                    air_temperature_c=self._at(air_temps, w_index),
                    water_temperature_c=self._at(sea_temps, index),
                    weather_code=code,
                    weather_description=describe_weather_code(code),
                    uv_index=self._at(uv, w_index),
                    visibility_km=None if visibility_m is None else round(visibility_m / 1000.0, 2),
                    sunrise=sunrise,
                    sunset=sunset,
                    raw={
                        "provider": self.name,
                        "hour_index": index,
                        # The hourly atmospheric block carries a probability, not
                        # a millimetre reading, so it is kept out of
                        # ``precipitation_mm`` and recorded here as what it is.
                        "precipitation_probability_pct": self._at(precip_prob, w_index),
                    },
                )
                snapshots.append(snapshot)

            return snapshots
        except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
            logger.warning("%s: fetch_forecast failed for %s (%s).", self.name, spot, exc)
            return []

    def health_check(self) -> tuple[bool, str]:
        """Probe the atmospheric endpoint with a one-hour request."""
        params = {
            "latitude": 0.0,
            "longitude": 0.0,
            "timezone": "auto",
            "timeformat": "unixtime",
            "forecast_days": 1,
            "current": "temperature_2m",
        }
        if self.api_key:
            params["apikey"] = self.api_key
        payload = self.request_json(self.forecast_url, params, timeout=min(self.timeout_seconds, 10))
        if payload is None:
            return False, str(_("Open-Meteo is unreachable. The app keeps working from stored readings."))
        if payload.get("error"):
            return False, str(_("Open-Meteo rejected the request."))
        return True, str(_("Open-Meteo responded normally."))
