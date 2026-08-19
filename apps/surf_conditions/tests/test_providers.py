"""Provider tests. No test in this file touches the network.

The two properties that matter are asserted directly: a provider never raises,
and a provider never invents a value.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import override_settings

from apps.core.enums import TideState
from apps.surf_conditions.providers import registry
from apps.surf_conditions.providers.base import BaseSurfProvider, ConditionSnapshot
from apps.surf_conditions.providers.metno import MetNoProvider, wmo_code_from_symbol
from apps.surf_conditions.providers.open_meteo import (
    OpenMeteoProvider,
    derive_tide_state,
    describe_weather_code,
    is_thunderstorm,
    weather_icon_name,
)


class _Spot:
    """Minimal stand-in — the providers only ever read these three attributes."""

    latitude = 38.28
    longitude = 26.37
    altitude = 5
    beach_facing_deg = 180.0

    def __str__(self) -> str:
        return "Stub Spot"


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------
def test_an_empty_snapshot_reports_itself_as_empty():
    assert ConditionSnapshot().is_empty is True
    assert ConditionSnapshot(wave_height_m=1.0).is_empty is False


def test_merge_fills_gaps_without_overwriting():
    marine = ConditionSnapshot(wave_height_m=1.2)
    weather = ConditionSnapshot(wave_height_m=9.9, wind_speed_kmh=15.0)
    merged = marine.merge(weather)
    assert merged.wave_height_m == 1.2
    assert merged.wind_speed_kmh == 15.0


def test_as_dict_is_json_safe():
    snapshot = ConditionSnapshot(recorded_at=datetime(2026, 8, 18, 6, 0, tzinfo=UTC))
    payload = snapshot.as_dict()
    assert payload["recorded_at"].startswith("2026-08-18")
    assert payload["sunrise"] is None


def test_coerce_float_refuses_nonsense_instead_of_guessing():
    assert BaseSurfProvider.coerce_float("1.5") == 1.5
    assert BaseSurfProvider.coerce_float("") is None
    assert BaseSurfProvider.coerce_float("north") is None
    assert BaseSurfProvider.coerce_float(float("nan")) is None


# ---------------------------------------------------------------------------
# WMO codes
# ---------------------------------------------------------------------------
def test_every_wmo_code_from_0_to_99_resolves_to_text():
    for code in range(100):
        assert describe_weather_code(code)


def test_unknown_or_missing_codes_return_an_empty_string():
    assert describe_weather_code(None) == ""
    assert describe_weather_code(1234) == ""
    assert describe_weather_code("sunny") == ""


def test_thunderstorm_codes_are_recognised():
    assert is_thunderstorm(95) is True
    assert is_thunderstorm(99) is True
    assert is_thunderstorm(3) is False
    assert is_thunderstorm(None) is False


def test_weather_icons_come_from_the_vendored_set():
    allowed = {"sun", "cloud", "cloud-rain", "zap"}
    for code in range(100):
        assert weather_icon_name(code) in allowed


# ---------------------------------------------------------------------------
# Tide derivation
# ---------------------------------------------------------------------------
def test_tide_is_unknown_without_a_usable_series():
    assert derive_tide_state([], 0) == TideState.UNKNOWN
    assert derive_tide_state([None, None, None], 1) == TideState.UNKNOWN


def test_a_tideless_sea_reports_unknown_rather_than_noise():
    flat = [0.01, 0.011, 0.012, 0.011, 0.010, 0.011, 0.012]
    assert derive_tide_state(flat, 3) == TideState.UNKNOWN


def test_the_peak_of_the_cycle_is_high_water():
    # A clean semi-diurnal shape peaking at index 6.
    series = [-1.0, -0.7, -0.2, 0.3, 0.7, 0.95, 1.0, 0.95, 0.7, 0.3, -0.2, -0.7, -1.0]
    assert derive_tide_state(series, 6) == TideState.HIGH


def test_the_trough_of_the_cycle_is_low_water():
    series = [1.0, 0.7, 0.2, -0.3, -0.7, -0.95, -1.0, -0.95, -0.7, -0.3, 0.2, 0.7, 1.0]
    assert derive_tide_state(series, 6) == TideState.LOW


def test_the_middle_of_the_cycle_reports_the_direction_of_travel():
    rising = [-1.0, -0.7, -0.2, 0.3, 0.7, 0.95, 1.0, 0.95, 0.7, 0.3, -0.2, -0.7, -1.0]
    assert derive_tide_state(rising, 3) == TideState.MID_RISING
    assert derive_tide_state(rising, 9) == TideState.MID_FALLING


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------
def test_open_meteo_declares_the_licence_credit():
    provider = OpenMeteoProvider()
    assert provider.attribution == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert provider.requires_api_key is False


def test_marine_parameters_use_the_swell_wave_prefix():
    # ``swell_height`` (without ``_wave``) is rejected by the API with HTTP 400.
    assert "swell_wave_height" in OpenMeteoProvider.MARINE_CURRENT
    assert "swell_wave_period" in OpenMeteoProvider.MARINE_HOURLY
    assert "swell_height," not in OpenMeteoProvider.MARINE_CURRENT


def test_sunrise_and_sunset_are_requested_from_the_daily_block_only():
    assert "sunrise" in OpenMeteoProvider.FORECAST_DAILY
    assert "sunrise" not in OpenMeteoProvider.FORECAST_HOURLY
    assert "sunrise" not in OpenMeteoProvider.FORECAST_CURRENT


def test_marine_requests_select_a_sea_cell(monkeypatch):
    captured: dict = {}

    def fake_request(self, url, params=None, **kwargs):
        captured["url"] = url
        captured["params"] = params
        return

    monkeypatch.setattr(BaseSurfProvider, "request_json", fake_request)
    provider = OpenMeteoProvider({"CACHE_SECONDS": 0, "TIMEOUT_SECONDS": 5})
    provider._payload(_Spot(), "marine", 7)

    assert captured["params"]["cell_selection"] == "sea"
    assert captured["params"]["timeformat"] == "unixtime"
    assert captured["params"]["timezone"] == "auto"


def test_fetch_current_returns_none_when_both_endpoints_fail(monkeypatch):
    monkeypatch.setattr(BaseSurfProvider, "request_json", lambda *a, **k: None)
    provider = OpenMeteoProvider({"CACHE_SECONDS": 0})
    assert provider.fetch_current(_Spot()) is None
    assert provider.fetch_forecast(_Spot()) == []


def test_fetch_current_parses_a_realistic_payload(monkeypatch):
    hours = [1_755_500_400 + 3600 * i for i in range(13)]
    marine = {
        "utc_offset_seconds": 10800,
        "current": {
            "time": hours[6],
            "wave_height": 1.1,
            "wave_period": 8.5,
            "wave_direction": 210,
            "swell_wave_height": 0.9,
            "swell_wave_period": 11.0,
            "swell_wave_direction": 205,
            "wind_wave_height": 0.3,
            "sea_surface_temperature": 22.4,
            "sea_level_height_msl": 0.9,
        },
        "hourly": {
            "time": hours,
            "sea_level_height_msl": [
                -1.0, -0.7, -0.2, 0.3, 0.7, 0.95, 1.0, 0.95, 0.7, 0.3, -0.2, -0.7, -1.0
            ],
        },
    }
    weather = {
        "utc_offset_seconds": 10800,
        "current": {
            "time": hours[6],
            "temperature_2m": 27.0,
            "precipitation": 0.0,
            "weather_code": 1,
            "cloud_cover": 12,
            "visibility": 24000,
            "wind_speed_10m": 14.0,
            "wind_direction_10m": 350,
            "wind_gusts_10m": 22.0,
            "uv_index": 7.0,
        },
        "daily": {
            "time": [hours[0]],
            "sunrise": [hours[0] + 3600],
            "sunset": [hours[0] + 3600 * 13],
        },
    }

    def fake_payload(self, spot, kind, days):
        return marine if kind == "marine" else weather

    monkeypatch.setattr(OpenMeteoProvider, "_payload", fake_payload)
    snapshot = OpenMeteoProvider({"CACHE_SECONDS": 0}).fetch_current(_Spot())

    assert snapshot is not None
    assert snapshot.wave_height_m == 1.1
    assert snapshot.swell_period_s == 11.0
    assert snapshot.water_temperature_c == 22.4
    assert snapshot.wind_speed_kmh == 14.0
    assert snapshot.visibility_km == 24.0
    assert snapshot.weather_description == describe_weather_code(1)
    assert snapshot.tide_state == TideState.HIGH
    assert snapshot.recorded_at.tzinfo is not None


def test_forecast_parsing_produces_one_snapshot_per_hour(monkeypatch):
    hours = [1_755_500_400 + 3600 * i for i in range(6)]
    marine = {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": hours,
            "wave_height": [0.8, 0.9, 1.0, 1.1, 1.0, 0.9],
            "swell_wave_period": [9, 9, 10, 10, 10, 9],
            "sea_level_height_msl": [-0.5, -0.2, 0.2, 0.5, 0.2, -0.2],
        },
    }
    weather = {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": hours,
            "wind_speed_10m": [10, 12, 14, 16, 18, 20],
            "wind_direction_10m": [0, 10, 20, 30, 40, 50],
            "weather_code": [0, 1, 2, 3, 61, 95],
            "precipitation_probability": [0, 0, 10, 20, 60, 90],
        },
        "daily": {"time": [hours[0]], "sunrise": [hours[0]], "sunset": [hours[0] + 3600 * 12]},
    }

    def fake_payload(self, spot, kind, days):
        return marine if kind == "marine" else weather

    monkeypatch.setattr(OpenMeteoProvider, "_payload", fake_payload)
    snapshots = OpenMeteoProvider({"CACHE_SECONDS": 0}).fetch_forecast(_Spot(), days=1)

    assert len(snapshots) == 6
    assert snapshots[0].wave_height_m == 0.8
    assert snapshots[-1].wind_speed_kmh == 20
    # The hourly atmospheric block gives a probability, not millimetres, and the
    # provider refuses to pretend otherwise.
    assert snapshots[-1].precipitation_mm is None
    assert snapshots[-1].raw["precipitation_probability_pct"] == 90


def test_an_implausible_sunrise_is_discarded_rather_than_shown(monkeypatch):
    day = 1_755_500_400
    weather = {
        "utc_offset_seconds": 0,
        "daily": {"time": [day], "sunrise": [day + 50_000], "sunset": [day + 1_000]},
    }
    provider = OpenMeteoProvider({"CACHE_SECONDS": 0})
    daylight = provider._daylight_by_date(weather)
    assert list(daylight.values())[0] == (None, None)


# ---------------------------------------------------------------------------
# met.no
# ---------------------------------------------------------------------------
def test_metno_reports_that_it_has_no_wave_model():
    provider = MetNoProvider({"METNO_USER_AGENT": "Test/1.0 (test@example.com)"})
    assert provider.provides_marine_data is False
    assert "MET Norway" in provider.attribution


def test_metno_refuses_to_call_without_a_user_agent():
    provider = MetNoProvider({"METNO_USER_AGENT": ""})
    assert provider.is_configured is False
    ok, message = provider.health_check()
    assert ok is False
    assert "User-Agent" in message


def test_metno_symbol_codes_map_onto_wmo_codes():
    assert wmo_code_from_symbol("clearsky_day") == 0
    assert wmo_code_from_symbol("partlycloudy_night") == 2
    assert wmo_code_from_symbol("heavyrain") == 65
    assert wmo_code_from_symbol("rainshowersandthunder_day") == 95
    assert wmo_code_from_symbol("") is None
    assert wmo_code_from_symbol("not-a-symbol") is None


def test_metno_leaves_every_wave_field_none(monkeypatch):
    payload = {
        "properties": {
            "meta": {"units": {}},
            "timeseries": [
                {
                    "time": "2026-08-18T06:00:00Z",
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": 24.0,
                                "cloud_area_fraction": 20.0,
                                "wind_from_direction": 350.0,
                                "wind_speed": 5.0,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "clearsky_day"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                }
            ],
        }
    }
    monkeypatch.setattr(MetNoProvider, "_payload", lambda self, spot: payload)
    snapshot = MetNoProvider({"METNO_USER_AGENT": "Test/1.0"}).fetch_current(_Spot())

    assert snapshot is not None
    assert snapshot.wind_speed_kmh == pytest.approx(18.0)
    assert snapshot.wave_height_m is None
    assert snapshot.swell_period_s is None
    assert snapshot.water_temperature_c is None
    assert snapshot.tide_state == TideState.UNKNOWN


def test_metno_never_raises_on_a_broken_payload(monkeypatch):
    monkeypatch.setattr(MetNoProvider, "_payload", lambda self, spot: {"properties": None})
    provider = MetNoProvider({"METNO_USER_AGENT": "Test/1.0"})
    assert provider.fetch_current(_Spot()) is None
    assert provider.fetch_forecast(_Spot()) == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_aliases_and_unknown_names_resolve_to_something_usable():
    assert registry.canonical_name("open_meteo") == "open-meteo"
    assert registry.canonical_name("met.no") == "metno"
    assert registry.canonical_name("nonsense") == registry.DEFAULT_PROVIDER
    assert registry.canonical_name(None) == registry.DEFAULT_PROVIDER


def test_commercial_mode_without_a_key_switches_to_metno():
    registry.reset_providers()
    surf = {
        "PROVIDER": "open-meteo",
        "COMMERCIAL_MODE": True,
        "OPEN_METEO_API_KEY": "",
        "METNO_USER_AGENT": "Test/1.0",
        "CACHE_SECONDS": 0,
        "TIMEOUT_SECONDS": 5,
    }
    with override_settings(SURF=surf):
        assert registry.resolve_provider_name() == "metno"
        assert "non-commercial" in registry.commercial_downgrade_reason()
        assert registry.get_surf_provider().name == "metno"
    registry.reset_providers()


def test_commercial_mode_with_a_paid_key_stays_on_open_meteo():
    registry.reset_providers()
    surf = {
        "PROVIDER": "open-meteo",
        "COMMERCIAL_MODE": True,
        "OPEN_METEO_API_KEY": "paid-key-placeholder",
        "CACHE_SECONDS": 0,
    }
    with override_settings(SURF=surf):
        assert registry.resolve_provider_name() == "open-meteo"
        assert registry.commercial_downgrade_reason() is None
    registry.reset_providers()


def test_a_paid_key_targets_the_customer_host():
    provider = OpenMeteoProvider(
        {
            "OPEN_METEO_API_KEY": "paid-key-placeholder",
            "OPEN_METEO_FORECAST_URL": "https://api.open-meteo.com/v1/forecast",
            "OPEN_METEO_MARINE_URL": "https://marine-api.open-meteo.com/v1/marine",
        }
    )
    assert provider.forecast_url.startswith("https://customer-api.open-meteo.com")
    assert provider.marine_url.startswith("https://customer-marine-api.open-meteo.com")


def test_health_report_never_raises(monkeypatch):
    def boom(self):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(OpenMeteoProvider, "health_check", boom)
    monkeypatch.setattr(MetNoProvider, "health_check", boom)
    registry.reset_providers()
    report = registry.health_report()
    assert set(report) == set(registry.PROVIDER_CLASSES)
    assert all(entry["ok"] is False for entry in report.values())
    registry.reset_providers()
