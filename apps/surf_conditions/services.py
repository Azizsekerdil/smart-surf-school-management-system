"""Business rules for surf conditions — above all, the surf score.

The score is arithmetic, not a language model
---------------------------------------------
:func:`calculate_surf_score` is deterministic. The same reading always produces
the same number, on any machine, with the network unplugged. That property is
not a performance choice: a coach decides whether beginners go in the water
partly on this number, and a figure that cannot be reproduced or explained has
no business informing that decision. The AI may narrate the result; it never
produces it, and every stored score carries ``is_ai_generated=False``.

Weighting
---------
====================  ======  ==================================================
Component             Weight  What it measures
====================  ======  ==================================================
Wave height fit        35 %   Height against ``WAVE_HEIGHT_SUITABILITY[level]``
Wind quality           25 %   Direction relative to the beach, then strength
Swell period           15 %   Longer period = cleaner, more organised waves
Tide match             10 %   Distance from ``spot.ideal_tide`` on the cycle
Water temperature       5 %   How long the group can actually stay in
Weather / rain         10 %   Rain, fog, visibility, lightning
====================  ======  ==================================================

A component with no data is dropped and the remaining weights are renormalised,
so a met.no reading (no marine model) never gets a wave score invented for it.
Wave height is the one component that cannot be missing: without it there is no
surf score at all, and the function says so rather than guessing.

The hard safety gate
--------------------
Three conditions override every other component. If the wave exceeds
``max_safe`` for the level, or the wind exceeds ``MAX_WIND_KMH[level]``, or there
is lightning, then ``is_safe`` is ``False`` and the score is capped at 25 no
matter how good everything else looks. Perfect offshore wind on a 4 m day is
still a 4 m day for a beginner.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.enums import (
    MAX_WIND_KMH,
    WAVE_HEIGHT_SUITABILITY,
    SurfLevel,
    TideState,
    WindType,
    recommended_wetsuit,
    wind_type_from_directions,
)

from .models import ConditionForecast, SurfCondition, SurfScore, compass_label, score_band
from .providers.registry import commercial_downgrade_reason, get_surf_provider

logger = logging.getLogger("apps.surf_conditions")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: Component weights. They sum to 1.0; missing components are renormalised out.
SCORE_WEIGHTS: dict[str, float] = {
    "wave_height": 0.35,
    "wind": 0.25,
    "period": 0.15,
    "tide": 0.10,
    "water_temperature": 0.05,
    "weather": 0.10,
}

#: Ceiling applied to any score once the hard safety gate fires.
UNSAFE_SCORE_CAP = 25

#: The tide cycle, in order. Distance around this ring is the tide match.
TIDE_CYCLE: tuple[str, ...] = (
    TideState.LOW,
    TideState.MID_RISING,
    TideState.HIGH,
    TideState.MID_FALLING,
)

#: Wind class -> base quality before the strength penalty is applied.
WIND_TYPE_BASE: dict[str, float] = {
    WindType.GLASSY: 100.0,
    WindType.OFFSHORE: 100.0,
    WindType.CROSS_OFFSHORE: 82.0,
    WindType.CROSS_SHORE: 58.0,
    WindType.CROSS_ONSHORE: 38.0,
    WindType.ONSHORE: 22.0,
}

#: At or below this speed the water is glassy whatever the bearing says.
GLASSY_MAX_KMH = 5.0

#: Water temperature (°C) -> comfort score. Read top-down, first match wins.
WATER_TEMP_BANDS: tuple[tuple[float, float], ...] = (
    (24.0, 100.0),
    (21.0, 92.0),
    (18.0, 80.0),
    (15.0, 66.0),
    (12.0, 50.0),
    (9.0, 32.0),
    (-99.0, 15.0),
)

#: Hours retained for past forecast rows before they are purged.
FORECAST_RETENTION = timedelta(days=2)

#: A window is "best" if it stays within this many points of the day's peak.
BEST_WINDOW_TOLERANCE = 10

#: Scores are only meaningful in daylight — nobody teaches a lesson at 03:00.
DEFAULT_DAY_START = time(6, 0)
DEFAULT_DAY_END = time(20, 0)

#: The uniform "there is genuinely nothing here" reply expected by
#: ``apps.ai.tools``. Kept as a literal so the two modules stay decoupled.
NO_DATA_STATUS = "__no_data__"


def _no_data(message: str) -> dict:
    return {"status": NO_DATA_STATUS, "message": message, "count": 0, "results": []}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _thresholds_for(level: str) -> tuple[float, float, float, float]:
    """Wave-height thresholds for *level*, defaulting to the beginner table."""
    return WAVE_HEIGHT_SUITABILITY.get(level, WAVE_HEIGHT_SUITABILITY[SurfLevel.BEGINNER])


def _max_wind_for(level: str) -> float:
    return MAX_WIND_KMH.get(level, MAX_WIND_KMH[SurfLevel.BEGINNER])


def _factor(key: str, name: str, value: str, score: float | None, weight: float, note: str) -> dict:
    """One row of the explanation table.

    ``score`` is ``None`` when the input was not measured; such a factor is shown
    greyed out and excluded from the weighted average.
    """
    return {
        "key": key,
        "name": str(name),
        "value": str(value),
        "score": None if score is None else int(round(score)),
        "weight": int(round(weight * 100)),
        "note": str(note),
        "measured": score is not None,
    }


# ---------------------------------------------------------------------------
# The components
# ---------------------------------------------------------------------------
def _wave_height_factor(condition: SurfCondition, level: str) -> dict:
    """35 % — is the wave the right size for this level?

    Below ``minimum`` there is nothing to ride; between ``ideal_low`` and
    ``ideal_high`` it is exactly right; above ``max_safe`` the level must not be
    in the water and the gate fires separately.
    """
    minimum, ideal_low, ideal_high, max_safe = _thresholds_for(level)
    height = condition.wave_height_m
    weight = SCORE_WEIGHTS["wave_height"]
    name = _("Wave height")

    if height is None:
        return _factor(
            "wave_height",
            name,
            "—",
            None,
            weight,
            _("No wave data from this source."),
        )

    value = _("%(m).1f m (%(ft).1f ft)") % {
        "m": height,
        "ft": (condition.wave_height_ft or 0.0),
    }

    if height < minimum:
        score = 40.0 * (height / minimum) if minimum > 0 else 0.0
        note = _("Too small to ride — this level needs at least %(min).1f m.") % {"min": minimum}
    elif height < ideal_low:
        span = ideal_low - minimum
        score = 40.0 + 60.0 * ((height - minimum) / span) if span > 0 else 100.0
        note = _("Rideable but under the ideal %(low).1f–%(high).1f m band.") % {
            "low": ideal_low,
            "high": ideal_high,
        }
    elif height <= ideal_high:
        score = 100.0
        note = _("In the ideal %(low).1f–%(high).1f m band for this level.") % {
            "low": ideal_low,
            "high": ideal_high,
        }
    elif height <= max_safe:
        span = max_safe - ideal_high
        score = 100.0 - 80.0 * ((height - ideal_high) / span) if span > 0 else 20.0
        note = _("Above the ideal band and approaching the %(max).1f m limit.") % {
            "max": max_safe
        }
    else:
        score = 0.0
        note = _("Over the %(max).1f m hard limit for this level.") % {"max": max_safe}

    return _factor("wave_height", name, value, _clamp(score), weight, note)


def _wind_factor(condition: SurfCondition, level: str, spot) -> dict:
    """25 % — direction first, then strength.

    Offshore wind holds the wave face up and is the single biggest quality
    factor after size. Strength is judged against the level's own limit, so
    18 km/h is comfortable for an advanced group and already noticeable for a
    first-timer.
    """
    weight = SCORE_WEIGHTS["wind"]
    name = _("Wind")
    speed = condition.wind_speed_kmh
    direction = condition.wind_direction_deg

    if speed is None and direction is None:
        return _factor("wind", name, "—", None, weight, _("No wind data from this source."))

    wind_type = classify_wind(condition, spot)
    type_label = dict(WindType.choices).get(wind_type, wind_type)

    if speed is None:
        value = _("%(dir)s (%(type)s)") % {
            "dir": compass_label(direction),
            "type": type_label,
        }
        base = WIND_TYPE_BASE.get(wind_type, 58.0)
        return _factor(
            "wind",
            name,
            value,
            base,
            weight,
            _("Direction only — no wind speed reported, so strength is not scored."),
        )

    value = _("%(kmh).0f km/h (%(kt).0f kt) %(dir)s — %(type)s") % {
        "kmh": speed,
        "kt": condition.wind_knots or 0.0,
        "dir": compass_label(direction) if direction is not None else "—",
        "type": type_label,
    }

    base = WIND_TYPE_BASE.get(wind_type, 58.0)
    max_wind = _max_wind_for(level)
    ratio = speed / max_wind if max_wind > 0 else 1.0

    if ratio <= 0.3:
        penalty = 0.0
    elif ratio <= 1.0:
        penalty = 60.0 * ((ratio - 0.3) / 0.7)
    else:
        penalty = 60.0

    notes: list[str] = []
    if wind_type == WindType.GLASSY:
        notes.append(_("Glassy — the cleanest water this spot gets."))
    elif wind_type in (WindType.OFFSHORE, WindType.CROSS_OFFSHORE):
        notes.append(_("Blowing off the land, which grooms the wave face."))
    else:
        notes.append(_("Blowing onto the beach, which chops the wave up."))

    # Gusts matter more than the average for beginners holding a big board.
    gust = condition.wind_gust_kmh
    if gust is not None and gust > max_wind:
        penalty += 10.0
        notes.append(
            _("Gusting to %(gust).0f km/h, over the %(max).0f km/h limit for this level.")
            % {"gust": gust, "max": max_wind}
        )
    elif ratio > 1.0:
        notes.append(
            _("Over the %(max).0f km/h limit for this level.") % {"max": max_wind}
        )

    if level in (SurfLevel.FIRST_TIME, SurfLevel.BEGINNER) and wind_type == WindType.OFFSHORE and speed > 15:
        # Offshore is best for the wave and worst for a separated beginner: it
        # pushes them, and anything inflatable, away from the shore.
        notes.append(
            _("Offshore wind pushes a separated beginner out to sea — keep the group inshore.")
        )

    return _factor("wind", name, value, _clamp(base - penalty), weight, " ".join(str(n) for n in notes))


def _period_factor(condition: SurfCondition) -> dict:
    """15 % — a long period means organised swell rather than local wind slop."""
    weight = SCORE_WEIGHTS["period"]
    name = _("Swell period")
    period = condition.effective_period_s

    if period is None:
        return _factor("period", name, "—", None, weight, _("No period data from this source."))

    value = _("%(s).1f s") % {"s": period}

    if period < 4:
        score = 10.0
        note = _("Very short period — disorganised wind chop, not a surfable swell.")
    elif period < 6:
        score = 10.0 + 25.0 * ((period - 4) / 2.0)
        note = _("Under 6 s: choppy and weak.")
    elif period < 8:
        score = 35.0 + 30.0 * ((period - 6) / 2.0)
        note = _("Short period — the waves will be soft and close together.")
    elif period <= 12:
        score = 65.0 + 20.0 * ((period - 8) / 4.0)
        note = _("8–12 s: clean, well-spaced groundswell.")
    else:
        score = min(100.0, 85.0 + 15.0 * ((period - 12) / 4.0))
        note = _("Over 12 s: powerful long-period groundswell — waves break harder.")

    return _factor("period", name, value, _clamp(score), weight, note)


def _tide_factor(condition: SurfCondition, spot) -> dict:
    """10 % — how far the tide is from what this break wants."""
    weight = SCORE_WEIGHTS["tide"]
    name = _("Tide")
    tide = condition.tide_state
    ideal = getattr(spot, "ideal_tide", None)

    labels = dict(TideState.choices)
    if not tide or tide == TideState.UNKNOWN or tide not in TIDE_CYCLE:
        return _factor(
            "tide",
            name,
            str(labels.get(tide, "—")),
            None,
            weight,
            _("The sea-level model carries no usable tidal signal here."),
        )
    if not ideal or ideal not in TIDE_CYCLE:
        return _factor(
            "tide",
            name,
            str(labels.get(tide, "—")),
            None,
            weight,
            _("No ideal tide recorded for this spot."),
        )

    distance = min(
        (TIDE_CYCLE.index(tide) - TIDE_CYCLE.index(ideal)) % 4,
        (TIDE_CYCLE.index(ideal) - TIDE_CYCLE.index(tide)) % 4,
    )
    score = {0: 100.0, 1: 65.0, 2: 35.0}[distance]
    if distance == 0:
        note = _("Exactly the tide this break works best on.")
    elif distance == 1:
        note = _("One step off the ideal %(ideal)s.") % {"ideal": labels.get(ideal, ideal)}
    else:
        note = _("Opposite the ideal %(ideal)s.") % {"ideal": labels.get(ideal, ideal)}

    return _factor("tide", name, str(labels.get(tide, tide)), score, weight, note)


def _water_temperature_factor(condition: SurfCondition) -> dict:
    """5 % — how long the group can stay in before the cold ends the lesson."""
    weight = SCORE_WEIGHTS["water_temperature"]
    name = _("Water temperature")
    temperature = condition.water_temperature_c

    if temperature is None:
        return _factor(
            "water_temperature",
            name,
            "—",
            None,
            weight,
            _("No sea-surface temperature from this source."),
        )

    score = next(score for threshold, score in WATER_TEMP_BANDS if temperature >= threshold)
    value = _("%(c).1f °C") % {"c": temperature}
    note = _("Recommended wetsuit: %(suit)s.") % {"suit": recommended_wetsuit(temperature)}
    return _factor("water_temperature", name, value, score, weight, note)


def _weather_factor(condition: SurfCondition) -> dict:
    """10 % — rain, fog, visibility and lightning.

    Visibility is a rescue constraint, not a comfort one: an instructor who
    cannot see the outside of their group has lost supervision.
    """
    weight = SCORE_WEIGHTS["weather"]
    name = _("Weather")

    precipitation = condition.precipitation_mm
    probability = None
    raw = condition.raw_payload if isinstance(condition.raw_payload, dict) else {}
    candidate = raw.get("precipitation_probability_pct")
    if isinstance(candidate, (int, float)):
        probability = float(candidate)

    has_input = any(
        value is not None
        for value in (
            precipitation,
            probability,
            condition.weather_code,
            condition.cloud_cover_pct,
            condition.visibility_km,
        )
    )
    if not has_input:
        return _factor("weather", name, "—", None, weight, _("No weather data from this source."))

    score = 100.0
    notes: list[str] = []

    if precipitation is not None:
        if precipitation <= 0.05:
            pass
        elif precipitation <= 0.2:
            score -= 5.0
        elif precipitation <= 1.0:
            score -= 15.0
            notes.append(_("Light rain."))
        elif precipitation <= 3.0:
            score -= 30.0
            notes.append(_("Steady rain."))
        elif precipitation <= 6.0:
            score -= 50.0
            notes.append(_("Heavy rain."))
        else:
            score -= 70.0
            notes.append(_("Very heavy rain."))
    elif probability is not None:
        score -= min(30.0, probability * 0.3)
        if probability >= 50:
            notes.append(
                _("%(p).0f%% chance of precipitation this hour.") % {"p": probability}
            )

    if condition.cloud_cover_pct is not None and condition.cloud_cover_pct >= 90:
        score -= 5.0

    if condition.visibility_km is not None:
        if condition.visibility_km < 1.0:
            score = min(score, 25.0)
            notes.append(_("Visibility under 1 km — the water cannot be supervised properly."))
        elif condition.visibility_km < 3.0:
            score -= 20.0
            notes.append(_("Reduced visibility."))

    from .providers.open_meteo import FOG_CODES

    code = condition.weather_code
    if code is not None and int(code) in FOG_CODES:
        score = min(score, 45.0)
        notes.append(_("Fog."))
    if condition.has_lightning:
        score = min(score, 5.0)
        notes.append(_("Lightning — nobody goes in the water."))

    if condition.uv_index is not None and condition.uv_index >= 8:
        notes.append(_("UV index %(uv).0f — sun protection is mandatory.") % {"uv": condition.uv_index})

    value = condition.weather_description or _("Reported")
    if not notes:
        notes.append(_("Nothing in the weather is holding the session back."))

    return _factor("weather", name, value, _clamp(score), weight, " ".join(str(n) for n in notes))


# ---------------------------------------------------------------------------
# The score
# ---------------------------------------------------------------------------
def classify_wind(condition: SurfCondition, spot=None) -> str:
    """Classify this reading's wind against the spot's beach orientation."""
    spot = spot or getattr(condition, "spot", None)
    speed = condition.wind_speed_kmh
    if speed is not None and speed <= GLASSY_MAX_KMH:
        return WindType.GLASSY
    direction = condition.wind_direction_deg
    if direction is None or spot is None or getattr(spot, "beach_facing_deg", None) is None:
        return WindType.CROSS_SHORE
    return wind_type_from_directions(float(direction), float(spot.beach_facing_deg))


def _safety_gate(condition: SurfCondition, level: str) -> list[str]:
    """Reasons this level must not be in the water. Empty means no blocker."""
    reasons: list[str] = []
    _minimum, _low, _high, max_safe = _thresholds_for(level)
    max_wind = _max_wind_for(level)

    if condition.wave_height_m is not None and condition.wave_height_m > max_safe:
        reasons.append(
            _("Wave height %(h).1f m exceeds the %(max).1f m limit for this level.")
            % {"h": condition.wave_height_m, "max": max_safe}
        )
    if condition.wind_speed_kmh is not None and condition.wind_speed_kmh > max_wind:
        reasons.append(
            _("Wind %(w).0f km/h exceeds the %(max).0f km/h limit for this level.")
            % {"w": condition.wind_speed_kmh, "max": max_wind}
        )
    if condition.has_lightning:
        reasons.append(_("Lightning is reported — the water is closed regardless of size."))
    return [str(reason) for reason in reasons]


def _build_recommendation(score: int, level: str, gates: list[str], factors: list[dict]) -> str:
    level_label = dict(SurfLevel.choices).get(level, level)
    if gates:
        return str(
            _("Do not put %(level)s students in the water. %(reasons)s")
            % {"level": level_label, "reasons": " ".join(gates)}
        )

    weakest = sorted(
        (f for f in factors if f["score"] is not None), key=lambda f: f["score"]
    )
    tail = ""
    if weakest and weakest[0]["score"] < 60:
        tail = " " + str(
            _("Weakest factor: %(name)s — %(note)s")
            % {"name": weakest[0]["name"], "note": weakest[0]["note"]}
        )

    band = score_band(score)[0]
    if band == "excellent":
        head = _("Excellent conditions for %(level)s students.") % {"level": level_label}
    elif band == "good":
        head = _("Good conditions for %(level)s students.") % {"level": level_label}
    elif band == "fair":
        head = _("Workable for %(level)s students with close supervision.") % {
            "level": level_label
        }
    else:
        head = _("Poor conditions for %(level)s students — consider postponing.") % {
            "level": level_label
        }
    return str(head) + tail


def calculate_surf_score(condition: SurfCondition, level: str) -> dict:
    """Score one reading for one surf level.

    Returns ``{"level", "score", "factors", "is_safe", "recommendation",
    "has_data", "gates"}``.

    ``has_data`` is ``False`` when the reading has no wave height — met.no
    supplies no marine model, and a coastal grid cell can occasionally return
    nulls. In that case ``score`` is ``0`` and ``is_safe`` is ``False``: an
    unknown ocean is not a safe ocean, and the UI shows "no wave data" rather
    than a number.
    """
    spot = getattr(condition, "spot", None)

    factors = [
        _wave_height_factor(condition, level),
        _wind_factor(condition, level, spot),
        _period_factor(condition),
        _tide_factor(condition, spot),
        _water_temperature_factor(condition),
        _weather_factor(condition),
    ]

    gates = _safety_gate(condition, level)

    if condition.wave_height_m is None:
        return {
            "level": level,
            "score": 0,
            "factors": factors,
            "is_safe": False,
            "has_data": False,
            "gates": gates,
            "recommendation": str(
                _(
                    "No wave data is available for this spot, so no surf score can be "
                    "computed. Check the water in person before running a session."
                )
            ),
        }

    measured = [f for f in factors if f["score"] is not None]
    total_weight = sum(SCORE_WEIGHTS[f["key"]] for f in measured)
    if total_weight <= 0:
        weighted = 0.0
    else:
        weighted = (
            sum(SCORE_WEIGHTS[f["key"]] * f["score"] for f in measured) / total_weight
        )

    score = int(round(_clamp(weighted)))
    is_safe = not gates
    if not is_safe:
        score = min(score, UNSAFE_SCORE_CAP)

    return {
        "level": level,
        "score": score,
        "factors": factors,
        "is_safe": is_safe,
        "has_data": True,
        "gates": gates,
        "recommendation": _build_recommendation(score, level, gates, factors),
    }


def score_all_levels(condition: SurfCondition) -> dict[str, dict]:
    """Every level's score for one reading, in level order."""
    return {level: calculate_surf_score(condition, level) for level, _label in SurfLevel.choices}


# ---------------------------------------------------------------------------
# Storing a reading
# ---------------------------------------------------------------------------
#: Fields copied from a snapshot onto a :class:`SurfCondition`.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "wave_height_m",
    "wave_period_s",
    "wave_direction_deg",
    "swell_height_m",
    "swell_period_s",
    "swell_direction_deg",
    "wind_wave_height_m",
    "wind_speed_kmh",
    "wind_gust_kmh",
    "wind_direction_deg",
    "sea_level_height_msl_m",
    "air_temperature_c",
    "water_temperature_c",
    "weather_code",
    "uv_index",
    "precipitation_mm",
    "cloud_cover_pct",
    "visibility_km",
    "sunrise",
    "sunset",
)


def _condition_defaults(spot, snapshot, provider_name: str) -> dict:
    values = {field: getattr(snapshot, field) for field in SNAPSHOT_FIELDS}
    values["weather_description"] = (snapshot.weather_description or "")[:120]
    values["tide_state"] = snapshot.tide_state or TideState.UNKNOWN
    values["provider"] = provider_name[:40]
    values["source"] = SurfCondition.Source.PROVIDER
    values["raw_payload"] = snapshot.raw or {}

    direction = snapshot.wind_direction_deg
    speed = snapshot.wind_speed_kmh
    if speed is not None and speed <= GLASSY_MAX_KMH:
        values["wind_type"] = WindType.GLASSY
    elif direction is not None and getattr(spot, "beach_facing_deg", None) is not None:
        values["wind_type"] = wind_type_from_directions(
            float(direction), float(spot.beach_facing_deg)
        )
    else:
        values["wind_type"] = ""
    return values


def store_snapshot(spot, snapshot, *, provider_name: str, is_forecast: bool) -> SurfCondition:
    """Persist one snapshot as a :class:`SurfCondition` (idempotent).

    ``all_objects`` is used deliberately: the unique constraint spans
    soft-deleted rows, so re-fetching an hour that was archived must revive it
    rather than raise an integrity error.
    """
    defaults = _condition_defaults(spot, snapshot, provider_name)
    defaults["is_deleted"] = False
    defaults["deleted_at"] = None
    condition, _created = SurfCondition.all_objects.update_or_create(
        spot=spot,
        recorded_at=snapshot.recorded_at,
        is_forecast=is_forecast,
        defaults=defaults,
    )
    return condition


def score_condition(condition: SurfCondition) -> list[SurfScore]:
    """Compute and store one :class:`SurfScore` per surf level.

    Returns ``[]`` when the reading has no wave height — a stored score with no
    wave behind it would be a fabricated safety signal.
    """
    if condition.wave_height_m is None:
        SurfScore.objects.filter(condition=condition).delete()
        return []

    stored: list[SurfScore] = []
    for level, _label in SurfLevel.choices:
        result = calculate_surf_score(condition, level)
        score, _created = SurfScore.objects.update_or_create(
            condition=condition,
            level=level,
            defaults={
                "score": result["score"],
                "factors": result["factors"],
                "recommendation": result["recommendation"],
                "is_safe_for_level": result["is_safe"],
                # The number is arithmetic. Nothing here came from a model.
                "is_ai_generated": False,
            },
        )
        stored.append(score)
    return stored


# ---------------------------------------------------------------------------
# Refreshing
# ---------------------------------------------------------------------------
def refresh_spot_conditions(spot, *, include_forecast: bool = True, provider=None):
    """Fetch, store and score the current conditions at *spot*.

    Never raises. Returns the stored :class:`SurfCondition`, or ``None`` when
    the provider had nothing to give — in which case the screens keep showing
    the last good reading, flagged as stale.
    """
    try:
        provider = provider or get_surf_provider()
        snapshot = provider.fetch_current(spot)
        if snapshot is None or snapshot.recorded_at is None:
            logger.info("No current conditions returned for %s from %s.", spot, provider.name)
            return None

        with transaction.atomic():
            condition = store_snapshot(
                spot, snapshot, provider_name=provider.name, is_forecast=False
            )
            score_condition(condition)

        if include_forecast:
            refresh_spot_forecast(spot, provider=provider)
        return condition
    except Exception as exc:  # noqa: BLE001 - a refresh must never break a page or a task
        logger.warning("Refreshing conditions for %s failed: %s", spot, exc)
        return None


def refresh_spot_forecast(spot, *, days: int = 7, provider=None) -> int:
    """Store the hourly forecast and rebuild the daily rollups. Never raises.

    Returns the number of forecast hours stored.
    """
    try:
        provider = provider or get_surf_provider()
        snapshots = [s for s in provider.fetch_forecast(spot, days=days) if s.recorded_at]
        if not snapshots:
            return 0

        snapshots.sort(key=lambda item: item.recorded_at)
        start = snapshots[0].recorded_at
        end = snapshots[-1].recorded_at

        existing = {
            condition.recorded_at: condition
            for condition in SurfCondition.all_objects.filter(
                spot=spot, is_forecast=True, recorded_at__gte=start, recorded_at__lte=end
            )
        }

        to_create: list[SurfCondition] = []
        to_update: list[SurfCondition] = []
        update_fields = list(SNAPSHOT_FIELDS) + [
            "weather_description",
            "tide_state",
            "provider",
            "source",
            "raw_payload",
            "wind_type",
            "is_deleted",
            "deleted_at",
            "updated_at",
        ]

        for snapshot in snapshots:
            defaults = _condition_defaults(spot, snapshot, provider.name)
            defaults["is_deleted"] = False
            defaults["deleted_at"] = None
            condition = existing.get(snapshot.recorded_at)
            if condition is None:
                to_create.append(
                    SurfCondition(
                        spot=spot,
                        recorded_at=snapshot.recorded_at,
                        is_forecast=True,
                        **defaults,
                    )
                )
            else:
                for field, value in defaults.items():
                    setattr(condition, field, value)
                condition.updated_at = timezone.now()
                to_update.append(condition)

        with transaction.atomic():
            if to_create:
                SurfCondition.objects.bulk_create(to_create, batch_size=200)
            if to_update:
                SurfCondition.all_objects.bulk_update(to_update, update_fields, batch_size=200)
            # Past forecast hours are derived data with no evidential value —
            # remove them for real so the table does not grow without bound.
            SurfCondition.all_objects.filter(
                spot=spot,
                is_forecast=True,
                recorded_at__lt=timezone.now() - FORECAST_RETENTION,
            ).hard_delete()

        rebuild_daily_forecasts(spot)
        return len(snapshots)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Refreshing the forecast for %s failed: %s", spot, exc)
        return 0


def refresh_all_spot_conditions(*, days: int = 7) -> dict:
    """Refresh every active spot. Never raises; called by Celery and by cron."""
    from apps.locations.models import SurfSpot

    provider = get_surf_provider()
    spots = list(SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name"))

    refreshed, failed = [], []
    for spot in spots:
        condition = refresh_spot_conditions(spot, include_forecast=True, provider=provider)
        (refreshed if condition is not None else failed).append(spot.name)

    logger.info(
        "Surf conditions refreshed via %s: %s ok, %s failed.",
        provider.name,
        len(refreshed),
        len(failed),
    )
    return {
        "provider": provider.name,
        "spots": len(spots),
        "refreshed": refreshed,
        "failed": failed,
        "generated_at": timezone.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Daily rollups & best window
# ---------------------------------------------------------------------------
def _daylight_bounds(conditions: list[SurfCondition]) -> tuple[time, time]:
    """Local first and last hour worth scoring, from sunrise/sunset if known."""
    sunrise = next((c.sunrise for c in conditions if c.sunrise), None)
    sunset = next((c.sunset for c in conditions if c.sunset), None)
    start = timezone.localtime(sunrise).time() if sunrise else DEFAULT_DAY_START
    end = timezone.localtime(sunset).time() if sunset else DEFAULT_DAY_END
    if end <= start:
        return DEFAULT_DAY_START, DEFAULT_DAY_END
    return start, end


def _hour_rows(conditions: list[SurfCondition], level: str) -> list[dict]:
    """Per-hour data plus the computed score for *level*."""
    rows: list[dict] = []
    for condition in conditions:
        local = timezone.localtime(condition.recorded_at)
        result = calculate_surf_score(condition, level)
        rows.append(
            {
                "time": local.strftime("%H:%M"),
                "hour": local.hour,
                "iso": condition.recorded_at.isoformat(),
                "wave_height_m": condition.wave_height_m,
                "wave_period_s": condition.effective_period_s,
                "wind_speed_kmh": condition.wind_speed_kmh,
                "wind_gust_kmh": condition.wind_gust_kmh,
                "wind_direction_deg": condition.wind_direction_deg,
                "wind_type": condition.wind_type,
                "tide_state": condition.tide_state,
                "air_temperature_c": condition.air_temperature_c,
                "water_temperature_c": condition.water_temperature_c,
                "weather_code": condition.weather_code,
                "score": result["score"] if result["has_data"] else None,
                "is_safe": result["is_safe"],
            }
        )
    return rows


def _longest_best_run(rows: list[dict]) -> tuple[int | None, int | None, int | None]:
    """Indices of the longest run of hours within tolerance of the day's peak."""
    scored = [row for row in rows if row["score"] is not None]
    if not scored:
        return None, None, None
    peak = max(row["score"] for row in scored)
    if peak <= 0:
        return None, None, peak

    threshold = peak - BEST_WINDOW_TOLERANCE
    best_start = best_end = None
    best_length = 0
    run_start = None
    for index, row in enumerate(rows):
        qualifies = row["score"] is not None and row["score"] >= threshold and row["is_safe"]
        if qualifies:
            if run_start is None:
                run_start = index
            length = index - run_start + 1
            if length > best_length:
                best_length, best_start, best_end = length, run_start, index
        else:
            run_start = None
    return best_start, best_end, peak


def best_time_window(spot, day: date_cls, level: str) -> dict | None:
    """The stretch of *day* that scores highest at *spot* for *level*.

    Returns ``{"start", "end", "score", "hours"}`` with ``datetime.time``
    boundaries, or ``None`` when the day has no scoreable hour.
    """
    conditions = _conditions_for_day(spot, day)
    if not conditions:
        return None

    day_start, day_end = _daylight_bounds(conditions)
    daylight = [
        condition
        for condition in conditions
        if day_start <= timezone.localtime(condition.recorded_at).time() <= day_end
    ]
    rows = _hour_rows(daylight or conditions, level)
    start_index, end_index, peak = _longest_best_run(rows)
    if start_index is None or end_index is None:
        return None

    start_time = time(rows[start_index]["hour"], 0)
    end_hour = rows[end_index]["hour"]
    end_time = time(23, 59) if end_hour >= 23 else time(end_hour + 1, 0)
    return {
        "start": start_time,
        "end": end_time,
        "score": peak,
        "hours": rows[start_index : end_index + 1],
    }


def _conditions_for_day(spot, day: date_cls) -> list[SurfCondition]:
    """Every stored hour for a local calendar day, observations preferred."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = start + timedelta(days=1)

    # ``select_related`` is not optional here: scoring every hour reads
    # ``condition.spot.beach_facing_deg``, which would otherwise be one query
    # per hour per level.
    rows = list(
        SurfCondition.objects.filter(spot=spot, recorded_at__gte=start, recorded_at__lt=end)
        .select_related("spot")
        .order_by("recorded_at")
    )
    # When an observation and a forecast describe the same hour, the observation
    # wins: it is what actually happened.
    by_hour: dict[int, SurfCondition] = {}
    for row in rows:
        key = int(timezone.localtime(row.recorded_at).hour)
        current = by_hour.get(key)
        if current is None or (current.is_forecast and not row.is_forecast):
            by_hour[key] = row
    return [by_hour[key] for key in sorted(by_hour)]


def rebuild_daily_forecasts(spot, *, days: int = 7) -> list[ConditionForecast]:
    """Recompute the cached daily rollups for the next *days* days."""
    today = timezone.localdate()
    built: list[ConditionForecast] = []

    for offset in range(max(1, int(days))):
        day = today + timedelta(days=offset)
        conditions = _conditions_for_day(spot, day)
        if not conditions:
            continue

        day_start, day_end = _daylight_bounds(conditions)
        daylight = [
            condition
            for condition in conditions
            if day_start <= timezone.localtime(condition.recorded_at).time() <= day_end
        ] or conditions

        best_level, best_score = "", None
        best_rows: list[dict] = []
        for level, _label in SurfLevel.choices:
            rows = _hour_rows(daylight, level)
            scored = [row["score"] for row in rows if row["score"] is not None]
            if not scored:
                continue
            peak = max(scored)
            if best_score is None or peak > best_score:
                best_level, best_score, best_rows = level, peak, rows

        heights = [c.wave_height_m for c in conditions if c.wave_height_m is not None]
        winds = [c.wind_speed_kmh for c in conditions if c.wind_speed_kmh is not None]
        air = [c.air_temperature_c for c in conditions if c.air_temperature_c is not None]
        water = [c.water_temperature_c for c in conditions if c.water_temperature_c is not None]
        codes = [c.weather_code for c in conditions if c.weather_code is not None]

        # The representative code is the most severe of the daylight hours: a
        # thunderstorm at 15:00 must not be averaged away by a sunny morning.
        midday_codes = [
            c.weather_code for c in daylight if c.weather_code is not None
        ] or codes

        sunrise = next((c.sunrise for c in conditions if c.sunrise), None)
        sunset = next((c.sunset for c in conditions if c.sunset), None)

        window_start = window_end = None
        if best_rows:
            start_index, end_index, _peak = _longest_best_run(best_rows)
            if start_index is not None and end_index is not None:
                window_start = time(best_rows[start_index]["hour"], 0)
                end_hour = best_rows[end_index]["hour"]
                window_end = time(23, 59) if end_hour >= 23 else time(end_hour + 1, 0)

        summary = {
            "hours": _hour_rows(conditions, best_level or SurfLevel.BEGINNER),
            "wave_height_max": round(max(heights), 2) if heights else None,
            "wave_height_min": round(min(heights), 2) if heights else None,
            "wind_speed_max": round(max(winds), 1) if winds else None,
            "air_temperature_max": round(max(air), 1) if air else None,
            "water_temperature": round(sum(water) / len(water), 1) if water else None,
            "weather_code": max(midday_codes) if midday_codes else None,
            "best_score": best_score,
            "best_level": best_level,
            "sunrise": sunrise.isoformat() if sunrise else None,
            "sunset": sunset.isoformat() if sunset else None,
            "has_wave_data": bool(heights),
            "hour_count": len(conditions),
        }

        forecast, _created = ConditionForecast.all_objects.update_or_create(
            spot=spot,
            date=day,
            defaults={
                "generated_at": timezone.now(),
                "summary": summary,
                "best_window_start": window_start,
                "best_window_end": window_end,
                "best_level": best_level,
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        built.append(forecast)

    # Days that have rolled into the past are no longer a forecast of anything.
    ConditionForecast.all_objects.filter(spot=spot, date__lt=today).hard_delete()
    return built


# ---------------------------------------------------------------------------
# Reads used by the screens
# ---------------------------------------------------------------------------
def latest_condition(spot) -> SurfCondition | None:
    """The most recent observation at *spot* (never a forecast row)."""
    return (
        SurfCondition.objects.filter(spot=spot, is_forecast=False)
        .select_related("spot")
        .prefetch_related("scores")
        .order_by("-recorded_at")
        .first()
    )


def current_or_nearest(spot) -> SurfCondition | None:
    """The best available description of *now*.

    Prefers a fresh observation. Falls back to the forecast hour closest to now,
    clearly flagged as a forecast, rather than showing nothing at all.
    """
    observation = latest_condition(spot)
    if observation is not None and not observation.is_stale:
        return observation

    now = timezone.now()
    upcoming = (
        SurfCondition.objects.filter(
            spot=spot, is_forecast=True, recorded_at__gte=now - timedelta(hours=1)
        )
        .select_related("spot")
        .order_by("recorded_at")
        .first()
    )
    return upcoming or observation


def upcoming_forecasts(spot, *, days: int = 7) -> list[ConditionForecast]:
    today = timezone.localdate()
    return list(
        ConditionForecast.objects.filter(
            spot=spot, date__gte=today, date__lte=today + timedelta(days=days - 1)
        ).order_by("date")
    )


def hourly_chart_series(spot, *, hours: int = 48) -> dict:
    """Wave height and wind for the next *hours*, ready for Chart.js."""
    now = timezone.now()
    rows = list(
        SurfCondition.objects.filter(
            spot=spot,
            recorded_at__gte=now - timedelta(hours=1),
            recorded_at__lte=now + timedelta(hours=hours),
        ).order_by("recorded_at")
    )
    labels, wave, wind, gust = [], [], [], []
    for row in rows:
        labels.append(timezone.localtime(row.recorded_at).strftime("%d.%m %H:%M"))
        wave.append(row.wave_height_m)
        wind.append(row.wind_speed_kmh)
        gust.append(row.wind_gust_kmh)
    return {
        "labels": labels,
        "wave_height_m": wave,
        "wind_speed_kmh": wind,
        "wind_gust_kmh": gust,
        "has_wave_data": any(value is not None for value in wave),
        "has_wind_data": any(value is not None for value in wind),
        "has_gust_data": any(value is not None for value in gust),
    }


def dashboard_payload(spot) -> dict:
    """Everything the conditions dashboard needs for one spot."""
    provider = get_surf_provider()
    condition = current_or_nearest(spot)

    scores: list[dict] = []
    if condition is not None:
        stored = {score.level: score for score in condition.scores.all()}
        for level, label in SurfLevel.choices:
            score = stored.get(level)
            if score is None:
                computed = calculate_surf_score(condition, level)
                if not computed["has_data"]:
                    continue
                scores.append(
                    {
                        "level": level,
                        "label": str(label),
                        "score": computed["score"],
                        "band": score_band(computed["score"])[0],
                        "color": score_band(computed["score"])[1],
                        "is_safe": computed["is_safe"],
                        "recommendation": computed["recommendation"],
                        "factors": computed["factors"],
                        "suits_spot": spot.suits_level(level),
                        "stored": False,
                    }
                )
            else:
                scores.append(
                    {
                        "level": level,
                        "label": str(label),
                        "score": score.score,
                        "band": score.band,
                        "color": score.band_color,
                        "is_safe": score.is_safe_for_level,
                        "recommendation": score.recommendation,
                        "factors": score.factors or [],
                        "suits_spot": spot.suits_level(level),
                        "stored": True,
                    }
                )

    return {
        "spot": spot,
        "condition": condition,
        "scores": scores,
        "forecasts": upcoming_forecasts(spot),
        "chart": hourly_chart_series(spot),
        "provider_name": provider.name,
        "provider_label": provider.label,
        "attribution": provider.attribution,
        "provides_marine_data": provider.provides_marine_data,
        "licence_note": commercial_downgrade_reason(),
        "weights": {key: int(round(value * 100)) for key, value in SCORE_WEIGHTS.items()},
    }


# ---------------------------------------------------------------------------
# The AI tool contract
# ---------------------------------------------------------------------------
def _resolve_spot(spot_query: str | None):
    """Find the spot the caller means, or the school's default one."""
    from apps.locations.models import SurfSpot
    from apps.locations.services import get_primary_spot

    if spot_query:
        query = str(spot_query).strip()
        for lookup in ("code__iexact", "slug__iexact", "name__iexact", "name__icontains"):
            spot = SurfSpot.objects.filter(is_active=True, **{lookup: query}).first()
            if spot is not None:
                return spot
        return None
    return get_primary_spot()


def _condition_as_tool_dict(condition: SurfCondition) -> dict:
    return {
        "recorded_at": condition.recorded_at.isoformat() if condition.recorded_at else None,
        "is_forecast": condition.is_forecast,
        "is_stale": condition.is_stale,
        "provider": condition.provider,
        "wave_height_m": condition.wave_height_m,
        "wave_height_ft": condition.wave_height_ft,
        "swell_height_m": condition.swell_height_m,
        "swell_period_s": condition.effective_period_s,
        "swell_direction": compass_label(condition.swell_direction_deg),
        "wind_speed_kmh": condition.wind_speed_kmh,
        "wind_knots": condition.wind_knots,
        "wind_gust_kmh": condition.wind_gust_kmh,
        "wind_direction": compass_label(condition.wind_direction_deg),
        "wind_type": condition.wind_type,
        "tide_state": condition.tide_state,
        "air_temperature_c": condition.air_temperature_c,
        "water_temperature_c": condition.water_temperature_c,
        "recommended_wetsuit": condition.recommended_wetsuit,
        "weather": condition.weather_description,
        "uv_index": condition.uv_index,
        "precipitation_mm": condition.precipitation_mm,
        "visibility_km": condition.visibility_km,
        "sunrise": condition.sunrise.isoformat() if condition.sunrise else None,
        "sunset": condition.sunset.isoformat() if condition.sunset else None,
    }


def conditions_for_tool(spot_query: str | None = None, target_date: date_cls | None = None) -> dict:
    """Answer ``apps.ai.tools.get_surf_conditions``.

    Returns ``{"status": "ok", ...}`` with real stored values, or the module's
    no-data shape. It never invents a reading: when nothing has been fetched for
    a spot the assistant is told exactly that, and the system prompt forbids it
    from filling the gap itself.
    """
    spot = _resolve_spot(spot_query)
    if spot is None:
        if spot_query:
            return _no_data(
                _("No active surf spot matches “%(q)s”.") % {"q": spot_query}
            )
        return _no_data(_("No active surf spot is configured yet."))

    provider = get_surf_provider()
    today = timezone.localdate()
    day = target_date or today
    window = best_time_window(spot, day, SurfLevel.BEGINNER)

    if day < today:
        conditions = _conditions_for_day(spot, day)
        if not conditions:
            return _no_data(
                _("No conditions were recorded for %(spot)s on %(date)s.")
                % {"spot": spot.name, "date": day.isoformat()}
            )
        condition = conditions[len(conditions) // 2]
    elif day == today:
        condition = current_or_nearest(spot)
        if condition is None:
            return _no_data(
                _(
                    "No surf conditions have been fetched for %(spot)s yet. "
                    "Run the refresh before relying on a reading."
                )
                % {"spot": spot.name}
            )
    else:
        conditions = _conditions_for_day(spot, day)
        if not conditions:
            return _no_data(
                _("There is no forecast stored for %(spot)s on %(date)s.")
                % {"spot": spot.name, "date": day.isoformat()}
            )
        if window is not None:
            target_hour = window["start"].hour
            condition = min(
                conditions,
                key=lambda c: abs(timezone.localtime(c.recorded_at).hour - target_hour),
            )
        else:
            condition = conditions[len(conditions) // 2]

    levels = []
    for level, label in SurfLevel.choices:
        result = calculate_surf_score(condition, level)
        if not result["has_data"]:
            continue
        levels.append(
            {
                "level": level,
                "label": str(label),
                "score": result["score"],
                "is_safe": result["is_safe"],
                "recommendation": result["recommendation"],
                "blocking_reasons": result["gates"],
                "spot_accepts_level": spot.suits_level(level),
            }
        )

    payload = _condition_as_tool_dict(condition)

    result = {
        "status": "ok",
        "count": 1,
        "results": [payload],
        "spot": {
            "name": spot.name,
            "code": spot.code,
            "break_type": spot.break_type,
            "beach_facing_deg": spot.beach_facing_deg,
            "ideal_tide": spot.ideal_tide,
            "level_range": spot.level_range_display,
            "lifeguard_on_duty": spot.lifeguard_on_duty,
        },
        "date": day.isoformat(),
        "conditions": payload,
        "scores_by_level": levels,
        "best_window": (
            {
                "start": window["start"].strftime("%H:%M"),
                "end": window["end"].strftime("%H:%M"),
                "score": window["score"],
                "level": SurfLevel.BEGINNER,
            }
            if window
            else None
        ),
        "provider": provider.name,
        "attribution": provider.attribution,
        "note": (
            "Scores are computed arithmetically from the measured values against the "
            "school's published thresholds. They are not generated by a language model. "
            "A score never replaces a named staff member's decision to run a session."
        ),
    }
    if not levels:
        result["scores_unavailable_reason"] = str(
            _(
                "The active data source provides no wave model, so no surf score can be "
                "computed for this spot."
            )
        )
    return result
