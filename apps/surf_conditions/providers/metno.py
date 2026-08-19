"""met.no — the commercially clean fallback.

The Norwegian Meteorological Institute publishes ``locationforecast/2.0`` under
CC BY 4.0 with commercial use permitted, which is precisely what the free
Open-Meteo tier does not allow. When a school runs this product as a business
and has no paid Open-Meteo key, this is the source that keeps them legal.

What it cannot do
-----------------
met.no's ``locationforecast`` has **no marine model**: no wave height, no swell,
no period, no sea-surface temperature, no sea level. Those fields come back
``None`` and stay ``None``, which means no surf score can be computed from a
met.no reading — the dashboard says so in plain words instead of showing a
number nobody can stand behind. Wind, air temperature, precipitation and cloud
cover are genuine and useful for the safety picture.

Terms of service
----------------
An identifying ``User-Agent`` is mandatory; requests without one are answered
with HTTP 403. It comes from ``settings.SURF["METNO_USER_AGENT"]``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from apps.core.enums import TideState

from .base import BaseSurfProvider, ConditionSnapshot
from .open_meteo import describe_weather_code

logger = logging.getLogger("apps.surf_conditions")

#: met.no symbol code (suffix stripped) -> nearest WMO 4677 code, so the rest of
#: the application keeps one weather vocabulary regardless of the source.
METNO_SYMBOL_TO_WMO: dict[str, int] = {
    "clearsky": 0,
    "fair": 1,
    "partlycloudy": 2,
    "cloudy": 3,
    "fog": 45,
    "lightrain": 61,
    "rain": 63,
    "heavyrain": 65,
    "lightrainshowers": 80,
    "rainshowers": 81,
    "heavyrainshowers": 82,
    "lightsleet": 68,
    "sleet": 68,
    "heavysleet": 69,
    "lightsleetshowers": 83,
    "sleetshowers": 83,
    "heavysleetshowers": 84,
    "lightsnow": 71,
    "snow": 73,
    "heavysnow": 75,
    "lightsnowshowers": 85,
    "snowshowers": 85,
    "heavysnowshowers": 86,
    "lightrainandthunder": 95,
    "rainandthunder": 95,
    "heavyrainandthunder": 97,
    "lightrainshowersandthunder": 95,
    "rainshowersandthunder": 95,
    "heavyrainshowersandthunder": 97,
    "lightsleetandthunder": 95,
    "sleetandthunder": 95,
    "heavysleetandthunder": 97,
    "lightsleetshowersandthunder": 95,
    "sleetshowersandthunder": 95,
    "heavysleetshowersandthunder": 97,
    "lightsnowandthunder": 95,
    "snowandthunder": 95,
    "heavysnowandthunder": 97,
    "lightsnowshowersandthunder": 95,
    "snowshowersandthunder": 95,
    "heavysnowshowersandthunder": 97,
}

#: Suffixes met.no appends to say whether the sun was up. They carry no weather
#: information, so they are stripped before the lookup.
_DAYPART_SUFFIXES = ("_day", "_night", "_polartwilight")


def wmo_code_from_symbol(symbol_code: str | None) -> int | None:
    """Translate a met.no ``symbol_code`` into a WMO code (``None`` if unknown)."""
    if not symbol_code:
        return None
    key = str(symbol_code).strip().lower()
    for suffix in _DAYPART_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return METNO_SYMBOL_TO_WMO.get(key)


class MetNoProvider(BaseSurfProvider):
    """Wind, temperature, precipitation and cloud from MET Norway."""

    name = "metno"
    label = "MET Norway (met.no)"
    requires_api_key = False
    attribution = "Weather data from MET Norway (CC BY 4.0)"
    #: No wave model at all — this is the whole reason it is a fallback.
    provides_marine_data = False

    DEFAULT_FORECAST_DAYS = 7

    # -- configuration -----------------------------------------------------
    @property
    def url(self) -> str:
        return self.config.get("METNO_URL") or (
            "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        )

    @property
    def user_agent(self) -> str:
        return (self.config.get("METNO_USER_AGENT") or "").strip()

    @property
    def is_configured(self) -> bool:
        # met.no answers 403 without an identifying User-Agent, so an empty one
        # is a configuration error rather than a runtime failure.
        return bool(self.user_agent)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    # -- fetching ----------------------------------------------------------
    def _payload(self, spot) -> dict | None:
        if not self.is_configured:
            logger.warning(
                "%s: SURF['METNO_USER_AGENT'] is empty; met.no requires an "
                "identifying User-Agent and will refuse the request.",
                self.name,
            )
            return None

        key = self.cache_key(spot, "compact")
        cached = cache.get(key)
        if cached is not None:
            return cached

        params: dict[str, object] = {
            # met.no asks for coordinates truncated to four decimals so their
            # cache can be effective; sending more precision is discouraged.
            "lat": round(float(spot.latitude), 4),
            "lon": round(float(spot.longitude), 4),
        }
        altitude = self.coerce_int(getattr(spot, "altitude", None))
        if altitude is not None:
            params["altitude"] = altitude

        payload = self.request_json(self.url, params, headers=self._headers())
        if payload is None:
            return None
        if not (payload.get("properties") or {}).get("timeseries"):
            logger.warning("%s: response carried no timeseries for %s.", self.name, spot)
            return None
        if self.cache_seconds:
            cache.set(key, payload, self.cache_seconds)
        return payload

    # -- parsing -----------------------------------------------------------
    @staticmethod
    def _parse_time(value) -> datetime | None:
        if not value:
            return None
        text = str(value).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)

    def _snapshot_from_entry(self, entry: dict) -> ConditionSnapshot | None:
        moment = self._parse_time(entry.get("time"))
        if moment is None:
            return None

        data = entry.get("data") or {}
        instant = ((data.get("instant") or {}).get("details")) or {}
        next_hour = data.get("next_1_hours") or data.get("next_6_hours") or {}
        summary = (next_hour.get("summary") or {}).get("symbol_code")
        next_details = next_hour.get("details") or {}

        floats = self.coerce_float
        wind_ms = floats(instant.get("wind_speed"))
        gust_ms = floats(instant.get("wind_speed_of_gust"))
        code = wmo_code_from_symbol(summary)

        snapshot = ConditionSnapshot(
            recorded_at=moment,
            wind_speed_kmh=None if wind_ms is None else round(wind_ms * 3.6, 1),
            wind_gust_kmh=None if gust_ms is None else round(gust_ms * 3.6, 1),
            wind_direction_deg=floats(instant.get("wind_from_direction")),
            air_temperature_c=floats(instant.get("air_temperature")),
            cloud_cover_pct=floats(instant.get("cloud_area_fraction")),
            precipitation_mm=floats(next_details.get("precipitation_amount")),
            # met.no publishes the *clear-sky* UV index. It is the conservative
            # (higher) figure, which is the right way to be wrong about sunburn.
            uv_index=floats(instant.get("ultraviolet_index_clear_sky")),
            weather_code=code,
            weather_description=describe_weather_code(code),
            # No sea-level model: the tide is genuinely unknown from this source.
            tide_state=TideState.UNKNOWN,
            raw={
                "provider": self.name,
                "symbol_code": summary,
                "instant": instant,
                "uv_index_is_clear_sky": instant.get("ultraviolet_index_clear_sky") is not None,
                "marine_data_available": False,
            },
        )
        return snapshot

    # -- the interface -----------------------------------------------------
    def fetch_current(self, spot) -> ConditionSnapshot | None:
        try:
            payload = self._payload(spot)
            if payload is None:
                return None
            series = (payload.get("properties") or {}).get("timeseries") or []
            if not series:
                return None

            now = datetime.now(tz=UTC)
            best_entry, best_gap = None, None
            for entry in series:
                moment = self._parse_time(entry.get("time"))
                if moment is None:
                    continue
                gap = abs((moment - now).total_seconds())
                if best_gap is None or gap < best_gap:
                    best_entry, best_gap = entry, gap

            if best_entry is None:
                return None
            snapshot = self._snapshot_from_entry(best_entry)
            if snapshot is None or snapshot.is_empty:
                return None
            snapshot.raw["units"] = (payload.get("properties") or {}).get("meta", {}).get(
                "units", {}
            )
            return snapshot
        except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
            logger.warning("%s: fetch_current failed for %s (%s).", self.name, spot, exc)
            return None

    def fetch_forecast(self, spot, days: int = 7) -> list[ConditionSnapshot]:
        try:
            payload = self._payload(spot)
            if payload is None:
                return []
            series = (payload.get("properties") or {}).get("timeseries") or []
            horizon = datetime.now(tz=UTC) + timedelta(days=max(1, int(days or 1)))

            snapshots: list[ConditionSnapshot] = []
            for entry in series:
                moment = self._parse_time(entry.get("time"))
                if moment is None or moment > horizon:
                    continue
                snapshot = self._snapshot_from_entry(entry)
                if snapshot is not None and not snapshot.is_empty:
                    snapshots.append(snapshot)
            snapshots.sort(key=lambda item: item.recorded_at)
            return snapshots
        except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
            logger.warning("%s: fetch_forecast failed for %s (%s).", self.name, spot, exc)
            return []

    def health_check(self) -> tuple[bool, str]:
        if not self.is_configured:
            return False, str(
                _("met.no needs an identifying User-Agent. Set METNO_USER_AGENT in the environment.")
            )
        payload = self.request_json(
            self.url,
            {"lat": 60.10, "lon": 9.58},
            headers=self._headers(),
            timeout=min(self.timeout_seconds, 10),
        )
        if payload is None:
            return False, str(_("met.no is unreachable. The app keeps working from stored readings."))
        if not (payload.get("properties") or {}).get("timeseries"):
            return False, str(_("met.no responded without a forecast series."))
        return True, str(_("met.no responded normally. No wave data is available from this source."))
