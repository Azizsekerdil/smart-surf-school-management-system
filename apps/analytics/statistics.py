"""Descriptive statistics, trend detection and forecasting.

Everything in this module is a **pure function over numbers**. No database, no
Django model, no network call, and above all no language model: a surf school
makes staffing and pricing decisions from these figures, so they must be
reproducible and auditable. Given the same input you always get the same output.

Design rules
------------
1. **Nothing raises.** Every function is safe on an empty list, a single point,
   a series of identical values, ``None`` holes and non-numeric junk. Where a
   statistic is undefined the function returns ``None`` (or the documented
   default) instead of an exception — a dashboard must never 500 because
   February had no bookings.
2. **Position-preserving where it matters.** ``moving_average``,
   ``weighted_moving_average``, ``exponential_smoothing`` and ``outliers``
   return results aligned to the *input* index, so a chart can plot the overlay
   directly against the same x-axis. Summary statistics simply skip holes.
3. **An unreliable forecast says so.** :func:`forecast` refuses to look
   confident when the history is too short or the trend explains too little of
   the variation. See its docstring for the exact rule.

Sample vs population
--------------------
``sample=True`` (the default) divides by ``n - 1`` (Bessel's correction). Our
series are almost always a *sample* of an ongoing process — the 30 days we
happen to have, not every day the school will ever trade — so the sample
estimator is the honest default. Pass ``sample=False`` when the series really is
the whole population (e.g. "all seven weekdays of one specific week").
"""

from __future__ import annotations

import math
import statistics as stdlib_statistics
from collections.abc import Iterable, Sequence
from decimal import Decimal

import numpy as np
from django.utils.translation import gettext_lazy as _

from apps.core.utils import percent_change as _core_percent_change

__all__ = [
    "mean",
    "median",
    "mode",
    "std_dev",
    "variance",
    "percentile",
    "quartiles",
    "iqr",
    "percent_change",
    "moving_average",
    "weighted_moving_average",
    "exponential_smoothing",
    "linear_regression",
    "trend",
    "correlation",
    "seasonality",
    "forecast",
    "outliers",
    "summarise",
]

#: Below this many points relative to the horizon a forecast is never presented
#: as reliable: you need at least two horizons of history to extrapolate one.
MIN_HISTORY_MULTIPLE = 2

#: r² below this means the straight line explains less than half the variation.
POOR_FIT_R_SQUARED = 0.5

#: r² above this, with plenty of history, is the only route to "high".
STRONG_FIT_R_SQUARED = 0.75

#: Total movement smaller than this fraction of the series' own scale reads as
#: flat rather than as a real direction. 1 % over the whole window is noise.
FLAT_RELATIVE_THRESHOLD = 0.01

#: Tukey's fence multiplier for IQR outlier detection.
IQR_FENCE = 1.5

#: |z| above this many sample standard deviations is an outlier.
ZSCORE_FENCE = 3.0

#: Hard ceiling on a forecast horizon, so a bad query parameter cannot ask for
#: ten thousand points.
MAX_FORECAST_PERIODS = 365


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------
def _to_float(value) -> float | None:
    """Coerce one value to a finite float, or ``None`` if it is not a number.

    ``Decimal`` (the type every money column uses), ``int``, ``float`` and
    numeric strings all convert. ``None``, ``""``, NaN and ±inf become ``None``
    so they can be treated as holes rather than poisoning an average.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        try:
            result = float(value)
        except (ValueError, OverflowError):
            return None
    else:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _aligned(values: Iterable | None) -> list[float | None]:
    """Coerce a sequence, keeping holes in place so indexes stay meaningful."""
    if values is None:
        return []
    try:
        return [_to_float(value) for value in values]
    except TypeError:  # not iterable
        return []


def _series(values: Iterable | None) -> list[float]:
    """Coerce a sequence to the numbers it actually contains, dropping holes."""
    return [value for value in _aligned(values) if value is not None]


# ---------------------------------------------------------------------------
# Centre
# ---------------------------------------------------------------------------
def mean(values: Iterable | None) -> float | None:
    """Arithmetic mean: the sum divided by the count.

    Returns ``None`` for an empty series (there is no average of nothing).
    Non-numeric entries and ``None`` holes are ignored, so 30 days with 4 blank
    days averages over the 26 that have a reading.
    """
    data = _series(values)
    if not data:
        return None
    return float(stdlib_statistics.fmean(data))


def median(values: Iterable | None) -> float | None:
    """The middle value once sorted; the mean of the middle two when *n* is even.

    Preferred over the mean whenever one enormous booking would drag the average
    somewhere no typical day ever reaches. Returns ``None`` for an empty series.
    """
    data = _series(values)
    if not data:
        return None
    return float(stdlib_statistics.median(data))


def mode(values: Iterable | None) -> float | None:
    """The most frequently occurring value.

    Ties are resolved by returning the **smallest** of the tied values, so the
    answer is deterministic across runs and database backends. Returns ``None``
    for an empty series. On continuous data (revenue, hours) the mode is rarely
    meaningful — it is here for counts and ratings, where it is.
    """
    data = _series(values)
    if not data:
        return None
    return float(min(stdlib_statistics.multimode(data)))


# ---------------------------------------------------------------------------
# Spread
# ---------------------------------------------------------------------------
def variance(values: Iterable | None, sample: bool = True) -> float | None:
    """Mean squared deviation from the mean.

    ``sample=True`` divides by ``n - 1`` and therefore needs at least two
    points; ``sample=False`` divides by ``n`` and needs one. Returns ``None``
    when there are too few points. A series of identical values has variance
    ``0.0`` — that is a real answer, not a missing one.
    """
    data = _series(values)
    if sample:
        if len(data) < 2:
            return None
        return float(stdlib_statistics.variance(data))
    if not data:
        return None
    return float(stdlib_statistics.pvariance(data))


def std_dev(values: Iterable | None, sample: bool = True) -> float | None:
    """Square root of the variance — spread in the same unit as the data.

    "Revenue averages ₺4 200 a day with a standard deviation of ₺900" tells an
    operator how unusual a ₺6 000 day is. Same ``None`` rules as
    :func:`variance`; zero variance gives ``0.0``.
    """
    result = variance(values, sample=sample)
    if result is None:
        return None
    return float(math.sqrt(result))


def percentile(values: Iterable | None, p) -> float | None:
    """The value below which *p* percent of the series falls.

    Uses numpy's linear interpolation between the two closest ranks, which is
    the same convention as ``numpy.percentile`` and Excel's ``PERCENTILE.INC``.
    ``p`` is clamped to ``[0, 100]``. Returns ``None`` for an empty series or a
    non-numeric ``p``.
    """
    data = _series(values)
    if not data:
        return None
    position = _to_float(p)
    if position is None:
        return None
    position = min(100.0, max(0.0, position))
    return float(np.percentile(np.asarray(data, dtype=float), position))


def quartiles(values: Iterable | None) -> dict[str, float] | None:
    """The 25th, 50th and 75th percentiles as ``{"q1", "q2", "q3"}``.

    Returns ``None`` for an empty series. With very few points the quartiles are
    computed but say little — check ``n`` before drawing conclusions from them.
    """
    data = _series(values)
    if not data:
        return None
    array = np.asarray(data, dtype=float)
    q1, q2, q3 = (float(value) for value in np.percentile(array, [25, 50, 75]))
    return {"q1": q1, "q2": q2, "q3": q3}


def iqr(values: Iterable | None) -> float | None:
    """Interquartile range: ``q3 - q1``, the spread of the middle half.

    Immune to extreme values in a way the standard deviation is not, which is
    why it drives :func:`outliers`. Returns ``None`` for an empty series.
    """
    parts = quartiles(values)
    if parts is None:
        return None
    return float(parts["q3"] - parts["q1"])


def percent_change(current, previous) -> float | None:
    """Percentage change from *previous* to *current*.

    Delegates to :func:`apps.core.utils.percent_change` so "up 12 %" means
    exactly the same thing on every screen in the product. Returns ``None`` when
    the change is undefined (both sides zero).
    """
    return _core_percent_change(current, previous)


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------
def moving_average(values: Iterable | None, window: int) -> list[float | None]:
    """Simple moving average, aligned to the input.

    Entry ``i`` is the mean of the *window* values ending at ``i``; the first
    ``window - 1`` entries are ``None`` because no full window exists yet. The
    returned list is always the same length as the input, so it can be plotted
    as an overlay on the raw series without re-indexing.

    A window containing a hole yields ``None`` for that position rather than an
    average of fewer points — a "7-day average" computed from 4 days would
    misrepresent itself. An invalid or oversized window returns all ``None``.
    """
    data = _aligned(values)
    size = len(data)
    try:
        window = int(window)
    except (TypeError, ValueError):
        return [None] * size
    if window < 1 or window > size:
        return [None] * size

    result: list[float | None] = [None] * (window - 1)
    for index in range(window - 1, size):
        chunk = data[index - window + 1 : index + 1]
        if any(value is None for value in chunk):
            result.append(None)
        else:
            result.append(float(stdlib_statistics.fmean(chunk)))  # type: ignore[arg-type]
    return result


def weighted_moving_average(
    values: Iterable | None, weights: Sequence | None
) -> list[float | None]:
    """Moving average that lets recent points count for more.

    ``weights`` defines the window: ``weights[0]`` applies to the **oldest**
    value in the window and ``weights[-1]`` to the newest, so ``[1, 2, 3]``
    means "the most recent day counts three times as much as the day before
    yesterday". Weights are normalised by their sum, so they need not add to 1.

    Aligned to the input like :func:`moving_average`. Returns all ``None`` when
    the weights are empty, non-numeric, sum to zero, or are longer than the
    series.
    """
    data = _aligned(values)
    size = len(data)
    coefficients = _aligned(weights)
    if not coefficients or any(value is None for value in coefficients):
        return [None] * size

    window = len(coefficients)
    total_weight = float(sum(coefficients))  # type: ignore[arg-type]
    if window > size or total_weight == 0:
        return [None] * size

    result: list[float | None] = [None] * (window - 1)
    for index in range(window - 1, size):
        chunk = data[index - window + 1 : index + 1]
        if any(value is None for value in chunk):
            result.append(None)
            continue
        weighted = sum(
            value * weight
            for value, weight in zip(chunk, coefficients, strict=True)  # type: ignore[operator]
        )
        result.append(float(weighted / total_weight))
    return result


def exponential_smoothing(values: Iterable | None, alpha: float = 0.3) -> list[float]:
    """Single exponential smoothing: ``s[t] = α·x[t] + (1-α)·s[t-1]``.

    Unlike a moving average this produces a value from the very first point
    (``s[0] = x[0]``), so the output has no leading gap. ``alpha`` controls
    responsiveness: 1.0 follows the raw data exactly, values near 0 barely move.
    Anything outside ``(0, 1]`` falls back to the 0.3 default.

    A hole carries the previous smoothed level forward rather than dropping the
    point, which keeps the output the same length as the input. An empty input
    returns an empty list.
    """
    data = _aligned(values)
    if not data:
        return []

    rate = _to_float(alpha)
    if rate is None or not (0.0 < rate <= 1.0):
        rate = 0.3

    result: list[float] = []
    level: float | None = None
    for value in data:
        if level is None:
            level = value
        elif value is not None:
            level = rate * value + (1.0 - rate) * level
        result.append(0.0 if level is None else float(level))
    return result


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def linear_regression(x: Iterable | None, y: Iterable | None):
    """Ordinary least-squares fit of ``y = slope·x + intercept``.

    Returns ``(slope, intercept, r_squared)``, or ``(None, None, None)`` when
    the fit is undefined: fewer than two usable pairs, or every ``x`` identical
    (a vertical line is not a function of x).

    ``r_squared`` is the share of the variation in ``y`` the line accounts for,
    from 0 (the line tells you nothing the mean did not) to 1 (every point sits
    on it). When ``y`` never varies, the residuals are exactly zero and
    ``r_squared`` is reported as ``1.0``: a flat line does describe flat data
    perfectly, though :func:`trend` will still call the direction "flat".

    Pairs where either side is missing are dropped; a longer sequence is
    truncated to the length of the shorter one.
    """
    # strict=False is deliberate: a longer sequence is truncated to the shorter.
    pairs = [
        (a, b)
        for a, b in zip(_aligned(x), _aligned(y), strict=False)
        if a is not None and b is not None
    ]
    if len(pairs) < 2:
        return None, None, None

    xs = np.asarray([pair[0] for pair in pairs], dtype=float)
    ys = np.asarray([pair[1] for pair in pairs], dtype=float)

    x_mean = float(xs.mean())
    y_mean = float(ys.mean())
    sxx = float(((xs - x_mean) ** 2).sum())
    if sxx == 0.0:
        return None, None, None

    sxy = float(((xs - x_mean) * (ys - y_mean)).sum())
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean

    residual_ss = float((((slope * xs + intercept) - ys) ** 2).sum())
    total_ss = float(((ys - y_mean) ** 2).sum())
    if total_ss == 0.0:
        r_squared = 1.0
    else:
        r_squared = 1.0 - (residual_ss / total_ss)
    r_squared = min(1.0, max(0.0, r_squared))

    return float(slope), float(intercept), float(r_squared)


def trend(values: Iterable | None) -> dict:
    """Describe where a series is heading.

    Fits a straight line against the position index (0, 1, 2, …) and reports:

    ``direction``   ``"up"`` / ``"down"`` / ``"flat"``. Flat means the total
                    movement across the whole window is under 1 % of the series'
                    own average level — real enough to plot, too small to act on.
    ``slope``       change per step (per day, per week — whatever a step is).
    ``change``      total change across the window, ``slope × (n - 1)``.
    ``r_squared``   how well a straight line describes the series.
    ``confidence``  ``"high"`` (r² ≥ 0.7 and ≥ 8 points), ``"medium"``
                    (r² ≥ 0.4 and ≥ 5 points), otherwise ``"low"``. Three points
                    on a perfect line are still three points.

    Fewer than two usable points returns a flat, low-confidence result with
    ``None`` figures rather than raising.
    """
    data = _series(values)
    size = len(data)
    if size < 2:
        return {
            "direction": "flat",
            "slope": None,
            "change": None,
            "intercept": None,
            "r_squared": None,
            "confidence": "low",
            "n": size,
        }

    slope, intercept, r_squared = linear_regression(range(size), data)
    if slope is None:
        return {
            "direction": "flat",
            "slope": None,
            "change": None,
            "intercept": None,
            "r_squared": None,
            "confidence": "low",
            "n": size,
        }

    total_change = slope * (size - 1)
    scale = abs(float(stdlib_statistics.fmean(data)))
    if scale == 0.0:
        scale = max(abs(value) for value in data)

    if scale > 0.0:
        is_flat = abs(total_change) < FLAT_RELATIVE_THRESHOLD * scale
    else:
        is_flat = slope == 0.0

    if is_flat:
        direction = "flat"
    elif slope > 0:
        direction = "up"
    else:
        direction = "down"

    if r_squared >= 0.7 and size >= 8:
        confidence = "high"
    elif r_squared >= 0.4 and size >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "direction": direction,
        "slope": float(slope),
        "change": float(total_change),
        "intercept": float(intercept),
        "r_squared": float(r_squared),
        "confidence": confidence,
        "n": size,
    }


#: Complete sentences, never assembled from fragments — a translator needs the
#: whole phrase to word it naturally.
_CORRELATION_LABELS: dict[tuple[str, str], object] = {
    ("very_strong", "positive"): _("Very strong positive relationship"),
    ("very_strong", "negative"): _("Very strong negative relationship"),
    ("strong", "positive"): _("Strong positive relationship"),
    ("strong", "negative"): _("Strong negative relationship"),
    ("moderate", "positive"): _("Moderate positive relationship"),
    ("moderate", "negative"): _("Moderate negative relationship"),
    ("weak", "positive"): _("Weak positive relationship"),
    ("weak", "negative"): _("Weak negative relationship"),
    ("negligible", "positive"): _("No meaningful relationship"),
    ("negligible", "negative"): _("No meaningful relationship"),
}

_CORRELATION_INSUFFICIENT = _("Not enough paired data to judge a relationship")
_CORRELATION_UNDEFINED = _("Undefined — one of the two series never changes")


def correlation(x: Iterable | None, y: Iterable | None) -> dict:
    """Pearson correlation coefficient plus a plain-language reading of it.

    Returns ``{"r", "strength", "direction", "label", "n"}``. ``r`` runs from
    -1 (perfect inverse) through 0 (no linear relationship) to +1 (perfect
    agreement). Strength bands on ``|r|``: ≥ 0.9 very strong, ≥ 0.7 strong,
    ≥ 0.5 moderate, ≥ 0.3 weak, below that negligible.

    ``r`` is ``None`` in two documented cases: fewer than three paired points
    (with two points ``r`` is always exactly ±1 and means nothing), or one of
    the series never varies (dividing by a zero standard deviation).

    **Correlation is not causation.** Ice-cream sales and drownings both rise in
    August. Treat this as a prompt to investigate, never as a finding.
    """
    # strict=False is deliberate: unpaired trailing points are simply ignored.
    pairs = [
        (a, b)
        for a, b in zip(_aligned(x), _aligned(y), strict=False)
        if a is not None and b is not None
    ]
    size = len(pairs)
    if size < 3:
        return {
            "r": None,
            "strength": "insufficient_data",
            "direction": "none",
            "label": _CORRELATION_INSUFFICIENT,
            "n": size,
        }

    xs = np.asarray([pair[0] for pair in pairs], dtype=float)
    ys = np.asarray([pair[1] for pair in pairs], dtype=float)
    x_mean = float(xs.mean())
    y_mean = float(ys.mean())
    sxx = float(((xs - x_mean) ** 2).sum())
    syy = float(((ys - y_mean) ** 2).sum())
    if sxx == 0.0 or syy == 0.0:
        return {
            "r": None,
            "strength": "undefined",
            "direction": "none",
            "label": _CORRELATION_UNDEFINED,
            "n": size,
        }

    sxy = float(((xs - x_mean) * (ys - y_mean)).sum())
    r = sxy / math.sqrt(sxx * syy)
    r = min(1.0, max(-1.0, r))

    magnitude = abs(r)
    if magnitude >= 0.9:
        strength = "very_strong"
    elif magnitude >= 0.7:
        strength = "strong"
    elif magnitude >= 0.5:
        strength = "moderate"
    elif magnitude >= 0.3:
        strength = "weak"
    else:
        strength = "negligible"

    direction = "negative" if r < 0 else "positive"
    return {
        "r": float(r),
        "strength": strength,
        "direction": direction,
        "label": _CORRELATION_LABELS[(strength, direction)],
        "n": size,
    }


def seasonality(values: Iterable | None, period: int) -> list[float | None] | None:
    """Average multiplicative index for each phase of a repeating cycle.

    With ``period=7`` on a daily series starting on a Monday you get seven
    factors: Monday's, Tuesday's … Sunday's. ``1.0`` is an ordinary day, ``1.4``
    means that phase typically runs 40 % above average, ``0.6`` that it runs
    40 % below. With ``period=12`` on a monthly series you get month factors —
    exactly what a seasonal business needs to separate "August is busy" from
    "we grew".

    Each factor is ``mean(values at that phase) / mean(all values)``. Returns
    ``None`` when the period is not a positive integer, the series is shorter
    than one full cycle, or the overall mean is zero (nothing to be a multiple
    of). Individual phases with no data are ``None`` inside the list.

    The caller is responsible for the alignment: index 0 must genuinely be the
    first phase of the cycle. Fewer than two complete cycles produces a figure
    that is descriptive of the data seen, not evidence of a repeating pattern.
    """
    data = _aligned(values)
    try:
        period = int(period)
    except (TypeError, ValueError):
        return None
    if period < 1 or len(data) < period:
        return None

    present = [value for value in data if value is not None]
    if not present:
        return None
    overall = float(stdlib_statistics.fmean(present))
    if overall == 0.0:
        return None

    indices: list[float | None] = []
    for phase in range(period):
        phase_values = [value for value in data[phase::period] if value is not None]
        if not phase_values:
            indices.append(None)
        else:
            indices.append(float(stdlib_statistics.fmean(phase_values) / overall))
    return indices


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------
def forecast(
    values: Iterable | None,
    periods: int = 7,
    method: str = "linear",
    *,
    clamp_negative: bool = True,
) -> dict:
    """Project a series forward, and be honest about how much to trust it.

    Methods
    -------
    ``"linear"``  extrapolate the least-squares trend line (the default).
    ``"mean"``    flat projection at the historical mean — the right answer for
                  a series with no trend.
    ``"naive"``   flat projection at the last observed value.

    An unrecognised method falls back to ``"linear"``.

    Returned keys
    -------------
    ``values``       the projected numbers, ``periods`` of them (empty when
                     there is no history at all).
    ``method``       the method actually used, which may differ from the one
                     requested when the data could not support it.
    ``confidence``   ``"high"`` / ``"medium"`` / ``"low"`` / ``"none"``.
    ``warning``      the single most important caveat, ready to display, or
                     ``None`` when there is genuinely nothing to warn about.
    ``warning_code`` machine-readable twin of ``warning``.
    ``warnings``     every caveat, as ``{"code", "message"}``.
    ``low_confidence`` convenience boolean for templates.
    ``n``, ``required_points``, ``slope``, ``r_squared`` for the detail panel.

    The reliability rule — non-negotiable
    -------------------------------------
    Confidence is forced to ``"low"`` and a warning is always returned when
    either the history is shorter than ``2 × periods`` points, or the trend line
    explains less than half the variation (r² < 0.5). Reaching ``"high"``
    additionally requires r² ≥ 0.75 and at least ``4 × periods`` points. A
    forecast built on three days of data is never allowed to look like a plan.
    """
    data = _series(values)
    size = len(data)

    try:
        horizon = int(periods)
    except (TypeError, ValueError):
        horizon = 7
    horizon = max(1, min(MAX_FORECAST_PERIODS, horizon))
    required = MIN_HISTORY_MULTIPLE * horizon

    base = {
        "values": [],
        "method": "none",
        "confidence": "none",
        "warning": None,
        "warning_code": None,
        "warnings": [],
        "low_confidence": True,
        "n": size,
        "periods": horizon,
        "required_points": required,
        "slope": None,
        "intercept": None,
        "r_squared": None,
    }

    if size == 0:
        message = _("There is no history in this period, so nothing can be projected.")
        base["warning"] = message
        base["warning_code"] = "no_data"
        base["warnings"] = [{"code": "no_data", "message": message}]
        return base

    if size == 1:
        message = _(
            "Only one data point exists. The projection simply repeats it and "
            "must not be used for planning."
        )
        return {
            **base,
            "values": [data[0]] * horizon,
            "method": "naive",
            "confidence": "low",
            "warning": message,
            "warning_code": "single_point",
            "warnings": [{"code": "single_point", "message": message}],
        }

    requested = str(method or "linear").strip().lower()
    if requested not in {"linear", "mean", "naive"}:
        requested = "linear"

    slope = intercept = r_squared = None
    if requested == "linear":
        slope, intercept, r_squared = linear_regression(range(size), data)
        if slope is None:  # cannot happen for n >= 2, but never crash on it
            requested = "mean"

    if requested == "linear":
        projected = [slope * (size + step) + intercept for step in range(horizon)]
    elif requested == "mean":
        level = float(stdlib_statistics.fmean(data))
        projected = [level] * horizon
    else:
        projected = [data[-1]] * horizon

    # Revenue, bookings and occupancy cannot go below zero. Only clamp when the
    # observed history never went negative — a genuinely signed series (profit,
    # net change) keeps its sign.
    if clamp_negative and min(data) >= 0:
        projected = [max(0.0, value) for value in projected]

    warnings: list[dict] = []
    insufficient = size < required
    if insufficient:
        warnings.append(
            {
                "code": "insufficient_history",
                "message": _(
                    "Only %(have)s data points are available for a %(horizon)s-period "
                    "projection; at least %(need)s are needed before it can be relied on."
                )
                % {"have": size, "horizon": horizon, "need": required},
            }
        )

    poor_fit = requested == "linear" and (
        r_squared is None or r_squared < POOR_FIT_R_SQUARED
    )
    if poor_fit:
        warnings.append(
            {
                "code": "weak_fit",
                "message": _(
                    "The trend line explains only %(pct)s%% of the variation, so this "
                    "projection is indicative only."
                )
                % {"pct": round((r_squared or 0.0) * 100, 1)},
            }
        )

    if requested in {"mean", "naive"}:
        warnings.append(
            {
                "code": "flat_projection",
                "message": _(
                    "This projection assumes no change over time; it carries the "
                    "current level forward."
                ),
            }
        )

    if insufficient or poor_fit:
        confidence = "low"
    elif requested != "linear":
        confidence = "medium"
    elif r_squared >= STRONG_FIT_R_SQUARED and size >= 4 * horizon:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        **base,
        "values": [float(value) for value in projected],
        "method": requested,
        "confidence": confidence,
        "warning": warnings[0]["message"] if warnings else None,
        "warning_code": warnings[0]["code"] if warnings else None,
        "warnings": warnings,
        "low_confidence": confidence in {"low", "none"},
        "slope": None if slope is None else float(slope),
        "intercept": None if intercept is None else float(intercept),
        "r_squared": None if r_squared is None else float(r_squared),
    }


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------
def outliers(values: Iterable | None, method: str = "iqr") -> list[int]:
    """Indexes of the points that do not belong with the rest.

    ``"iqr"`` (default) uses Tukey's fences: anything below ``q1 - 1.5·IQR`` or
    above ``q3 + 1.5·IQR``. Robust — one freak day cannot hide itself by
    inflating the threshold. Needs at least four points; with fewer, quartiles
    are meaningless and the function returns ``[]``. A constant middle half
    (IQR of zero) also returns ``[]``, because otherwise every value that
    differs at all would be flagged.

    ``"zscore"`` flags points more than three sample standard deviations from
    the mean. Needs at least three points and a non-zero standard deviation.

    Indexes refer to the **original** sequence, holes included, so they can be
    used directly to highlight points on a chart. An unrecognised method falls
    back to ``"iqr"``.
    """
    data = _aligned(values)
    present = [(index, value) for index, value in enumerate(data) if value is not None]
    if not present:
        return []

    approach = str(method or "iqr").strip().lower()
    if approach not in {"iqr", "zscore"}:
        approach = "iqr"

    numbers = [value for _index, value in present]

    if approach == "zscore":
        if len(numbers) < 3:
            return []
        centre = float(stdlib_statistics.fmean(numbers))
        spread = float(stdlib_statistics.stdev(numbers))
        if spread == 0.0:
            return []
        return [
            index
            for index, value in present
            if abs((value - centre) / spread) > ZSCORE_FENCE
        ]

    if len(numbers) < 4:
        return []
    parts = quartiles(numbers)
    if parts is None:
        return []
    spread = parts["q3"] - parts["q1"]
    if spread == 0.0:
        return []
    low = parts["q1"] - IQR_FENCE * spread
    high = parts["q3"] + IQR_FENCE * spread
    return [index for index, value in present if value < low or value > high]


# ---------------------------------------------------------------------------
# Everything at once
# ---------------------------------------------------------------------------
def summarise(values: Iterable | None) -> dict:
    """Every statistic above that applies to *values*, in one dictionary.

    This is what the dashboard's "Statistical summary" panel renders. Keys whose
    statistic is undefined for the given series are ``None`` rather than absent,
    so a template can address them unconditionally. An empty series returns the
    full shape with ``n = 0``.
    """
    data = _series(values)
    size = len(data)
    parts = quartiles(data)

    result = {
        "n": size,
        "sum": float(sum(data)) if data else 0.0,
        "mean": mean(data),
        "median": median(data),
        "mode": mode(data),
        "minimum": float(min(data)) if data else None,
        "maximum": float(max(data)) if data else None,
        "range": float(max(data) - min(data)) if data else None,
        "std_dev": std_dev(data, sample=True),
        "variance": variance(data, sample=True),
        "q1": parts["q1"] if parts else None,
        "q3": parts["q3"] if parts else None,
        "iqr": iqr(data),
        "p10": percentile(data, 10),
        "p90": percentile(data, 90),
        "first": data[0] if data else None,
        "last": data[-1] if data else None,
        "trend": trend(data),
        "outliers": outliers(data),
    }

    result["change_pct"] = (
        percent_change(result["last"], result["first"]) if size >= 2 else None
    )
    # Coefficient of variation: spread expressed as a share of the mean, which
    # makes "how erratic is this?" comparable between revenue and headcount.
    if result["mean"] and result["std_dev"] is not None and result["mean"] != 0:
        result["coefficient_of_variation"] = abs(result["std_dev"] / result["mean"])
    else:
        result["coefficient_of_variation"] = None
    return result
