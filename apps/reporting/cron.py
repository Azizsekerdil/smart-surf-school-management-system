"""A minimal five-field cron matcher.

Scheduled reports need one question answered: *should this definition run at
this minute?* Pulling in a dependency for that is not worth it, and the
expressions a surf school writes are simple ("every Monday at 07:00", "the 1st
of the month at 06:30").

Supported syntax, per field: ``*``, ``*/n``, ``a``, ``a-b``, ``a-b/n`` and comma
separated lists of those. Fields, in order::

    minute (0-59)  hour (0-23)  day-of-month (1-31)  month (1-12)  weekday (0-6, Sunday=0)

Named months and weekdays, ``@daily`` style shortcuts and the "either day-of-month
or weekday" quirk of Vixie cron are deliberately *not* supported: they are the
parts operators get wrong, and an expression this module rejects is reported to
the user instead of silently never firing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils.translation import gettext_lazy as _

FIELD_RANGES: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),
)


class CronError(ValueError):
    """Raised for an expression that can never be evaluated."""


@dataclass(frozen=True)
class CronSchedule:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]

    def matches(self, moment: datetime) -> bool:
        # Python's weekday(): Monday=0. Cron's: Sunday=0.
        weekday = (moment.weekday() + 1) % 7
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and moment.day in self.days
            and moment.month in self.months
            and weekday in self.weekdays
        )


def parse_cron(expression: str) -> CronSchedule:
    """Parse a five-field cron expression, raising :class:`CronError`."""
    parts = (expression or "").split()
    if len(parts) != 5:
        raise CronError(
            _("A schedule needs exactly five fields: minute hour day month weekday.")
        )

    parsed: list[frozenset[int]] = []
    for raw, (name, low, high) in zip(parts, FIELD_RANGES):
        parsed.append(_parse_field(raw, name, low, high))

    return CronSchedule(
        minutes=parsed[0],
        hours=parsed[1],
        days=parsed[2],
        months=parsed[3],
        weekdays=parsed[4],
    )


def _parse_field(raw: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError(_("Empty value in the %(field)s field.") % {"field": name})

        step = 1
        if "/" in chunk:
            chunk, _sep, step_raw = chunk.partition("/")
            if not step_raw.isdigit() or int(step_raw) < 1:
                raise CronError(
                    _("Invalid step in the %(field)s field.") % {"field": name}
                )
            step = int(step_raw)
            chunk = chunk or "*"

        if chunk == "*":
            start, end = low, high
        elif "-" in chunk:
            start_raw, _sep, end_raw = chunk.partition("-")
            start, end = _as_int(start_raw, name), _as_int(end_raw, name)
        else:
            start = end = _as_int(chunk, name)

        if start < low or end > high or start > end:
            raise CronError(
                _("The %(field)s field must be between %(low)s and %(high)s.")
                % {"field": name, "low": low, "high": high}
            )
        values.update(range(start, end + 1, step))

    if not values:
        raise CronError(_("The %(field)s field matches nothing.") % {"field": name})
    return frozenset(values)


def _as_int(raw: str, name: str) -> int:
    raw = raw.strip()
    if not raw.isdigit():
        raise CronError(
            _("“%(value)s” is not a number in the %(field)s field.")
            % {"value": raw, "field": name}
        )
    return int(raw)


def cron_is_due(expression: str, moment: datetime, window_minutes: int = 1) -> bool:
    """Would *expression* have fired in the ``window_minutes`` up to *moment*?

    The window exists because the scheduler is not guaranteed to tick on the
    exact minute — a task queue that runs every five minutes still has to catch
    a job written for ``0 7 * * *``.
    """
    try:
        schedule = parse_cron(expression)
    except CronError:
        return False

    from datetime import timedelta  # noqa: PLC0415 - local, keeps the module import-light

    for offset in range(max(window_minutes, 1)):
        candidate = (moment - timedelta(minutes=offset)).replace(second=0, microsecond=0)
        if schedule.matches(candidate):
            return True
    return False


def describe_cron(expression: str) -> str:
    """Human-readable confirmation that an expression parses."""
    try:
        parse_cron(expression)
    except CronError as error:
        return str(error)
    return str(_("Valid schedule."))


__all__ = ["CronError", "CronSchedule", "cron_is_due", "describe_cron", "parse_cron"]
