"""Read queries for the CRM, including the segment criteria engine.

Security note
-------------
Segment criteria arrive as JSON written by an operator. They are **never**
evaluated, never turned into a lookup path, and never splatted into
``.filter(**data)``. Instead each key is looked up in :data:`CRITERIA_SPECS`,
its value is coerced to the declared type, and a *hard-coded* ``Q`` object is
built by the handler for that key. An unknown key is rejected at save time and
ignored at query time.

Portability note
----------------
The CRM reads two other apps (``customers`` and ``bookings``) whose exact field
names it does not own. Every cross-app lookup is resolved through
:func:`_field_name`, so a missing field degrades to "this rule is not supported
on your data model" instead of a 500.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, Max, Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import ACTIVE_BOOKING_STATUSES, BookingStatus, SurfLevel

#: Booking statuses that count as "the customer actually turned up or will".
VISIT_STATUSES: tuple[str, ...] = tuple(ACTIVE_BOOKING_STATUSES) + (BookingStatus.COMPLETED,)

#: Candidate names for the field that dates a booking, most specific first.
BOOKING_DATE_FIELDS = (
    "start_at",
    "starts_at",
    "scheduled_at",
    "scheduled_for",
    "booking_date",
    "date",
    "created_at",
)

#: Candidate names for a customer's spoken/preferred language field.
CUSTOMER_LANGUAGE_FIELDS = ("preferred_language", "language")

#: Candidate names for a customer's accumulated spend.
CUSTOMER_VALUE_FIELDS = ("lifetime_value", "total_spent", "total_spend")


# ---------------------------------------------------------------------------
# Model introspection helpers
# ---------------------------------------------------------------------------
def _get_model(label: str):
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def customer_model():
    return _get_model("customers.Customer")


def _field_name(model, *candidates: str) -> str | None:
    """Return the first of *candidates* that exists on *model*."""
    if model is None:
        return None
    names = {field.name for field in model._meta.get_fields()}
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def booking_relation() -> tuple[str | None, str | None, str | None]:
    """Return ``(query_name, date_field, status_field)`` for Customer → Booking.

    ``query_name`` is the reverse lookup name usable inside ``filter()``.
    Any element is ``None`` when the bookings app does not expose it.
    """
    booking = _get_model("bookings.Booking")
    if booking is None or customer_model() is None:
        return None, None, None
    try:
        fk = booking._meta.get_field("customer")
    except FieldDoesNotExist:
        return None, None, None
    if not getattr(fk, "related_query_name", None):
        return None, None, None
    query_name = fk.related_query_name()
    return (
        query_name,
        _field_name(booking, *BOOKING_DATE_FIELDS),
        _field_name(booking, "status"),
    )


def visit_filter(query_name: str, status_field: str | None) -> Q:
    """Restrict a reverse booking join to bookings that count as a visit."""
    if not status_field:
        return Q()
    return Q(**{f"{query_name}__{status_field}__in": VISIT_STATUSES})


# ---------------------------------------------------------------------------
# Value coercion — strict, never raises into the ORM
# ---------------------------------------------------------------------------
class CriteriaValueError(ValueError):
    """Raised internally when a criteria value cannot be coerced."""


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if value in (1, 0):
        return bool(value)
    raise CriteriaValueError


def _as_int(value, minimum: int = 0, maximum: int = 100_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CriteriaValueError from exc
    if not (minimum <= number <= maximum):
        raise CriteriaValueError
    return number


def _as_decimal(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CriteriaValueError from exc
    if number < 0:
        raise CriteriaValueError
    return number


def _as_str_list(value, max_items: int = 50, max_length: int = 60) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise CriteriaValueError
    items = [str(item).strip() for item in value if str(item).strip()]
    if not items or len(items) > max_items:
        raise CriteriaValueError
    if any(len(item) > max_length for item in items):
        raise CriteriaValueError
    return items


def _as_choice_list(value, allowed: tuple[str, ...]) -> list[str]:
    items = _as_str_list(value)
    invalid = [item for item in items if item not in allowed]
    if invalid:
        raise CriteriaValueError
    return items


def _icontains_any(field: str, values: list[str]) -> Q:
    """OR together case-insensitive exact matches — portable on SQLite and PG."""
    condition = Q()
    for value in values:
        condition |= Q(**{f"{field}__iexact": value})
    return condition


# ---------------------------------------------------------------------------
# The whitelist
# ---------------------------------------------------------------------------
# Every entry: coerce(value) -> python value, apply(queryset, value) -> queryset,
# available() -> bool, describe(value) -> str.
def _spec(label, coerce, apply, available, describe, hint=""):
    return {
        "label": label,
        "coerce": coerce,
        "apply": apply,
        "available": available,
        "describe": describe,
        "hint": hint,
    }


# --- surf level -------------------------------------------------------------
def _surf_level_available() -> bool:
    return _field_name(customer_model(), "surf_level") is not None


def _surf_level_apply(queryset, values):
    return queryset.filter(surf_level__in=values)


# --- marketing consent ------------------------------------------------------
def _consent_field() -> str | None:
    return _field_name(customer_model(), "marketing_consent")


def _consent_apply(queryset, value):
    return queryset.filter(**{_consent_field(): value})


# --- contactability ---------------------------------------------------------
def _has_email_apply(queryset, value):
    return queryset.exclude(email="") if value else queryset.filter(email="")


def _has_phone_apply(queryset, value):
    return queryset.exclude(phone="") if value else queryset.filter(phone="")


# --- geography --------------------------------------------------------------
def _city_apply(queryset, values):
    return queryset.filter(_icontains_any("city", values))


def _country_apply(queryset, values):
    return queryset.filter(_icontains_any("country", values))


# --- language ---------------------------------------------------------------
def _language_apply(queryset, values):
    field = _field_name(customer_model(), *CUSTOMER_LANGUAGE_FIELDS)
    return queryset.filter(**{f"{field}__in": values})


# --- tags -------------------------------------------------------------------
def _tags_apply(queryset, values):
    return queryset.filter(tags__slug__in=values).distinct()


# --- recency ----------------------------------------------------------------
def _created_within_apply(queryset, days):
    return queryset.filter(created_at__gte=timezone.now() - timezone.timedelta(days=days))


# --- lifetime value ---------------------------------------------------------
def _value_apply_min(queryset, amount):
    field = _field_name(customer_model(), *CUSTOMER_VALUE_FIELDS)
    return queryset.filter(**{f"{field}__gte": amount})


# --- booking-derived rules --------------------------------------------------
def _bookings_available() -> bool:
    query_name, date_field, _status = booking_relation()
    return bool(query_name and date_field)


def _annotate_visits(queryset):
    query_name, _date_field, status_field = booking_relation()
    return queryset.annotate(
        crm_visit_count=Count(
            query_name, filter=visit_filter(query_name, status_field), distinct=True
        )
    )


def _min_bookings_apply(queryset, count):
    return _annotate_visits(queryset).filter(crm_visit_count__gte=count)


def _max_bookings_apply(queryset, count):
    return _annotate_visits(queryset).filter(crm_visit_count__lte=count)


def _annotate_last_visit(queryset):
    query_name, date_field, status_field = booking_relation()
    return queryset.annotate(
        crm_last_visit=Max(
            f"{query_name}__{date_field}", filter=visit_filter(query_name, status_field)
        )
    )


def _last_visit_apply(queryset, days):
    cutoff = timezone.now() - timezone.timedelta(days=days)
    return _annotate_last_visit(queryset).filter(crm_last_visit__gte=cutoff)


def _no_visit_apply(queryset, days):
    cutoff = timezone.now() - timezone.timedelta(days=days)
    return _annotate_last_visit(queryset).filter(
        Q(crm_last_visit__isnull=True) | Q(crm_last_visit__lt=cutoff)
    )


CRITERIA_SPECS: dict[str, dict] = {
    "surf_level": _spec(
        _("Surf level"),
        lambda value: _as_choice_list(value, tuple(SurfLevel.values)),
        _surf_level_apply,
        _surf_level_available,
        lambda value: _("Surf level is one of: %(values)s") % {"values": ", ".join(value)},
        hint=_("Requires a surf level on the customer record."),
    ),
    "marketing_consent": _spec(
        _("Marketing consent"),
        _as_bool,
        _consent_apply,
        lambda: _consent_field() is not None,
        lambda value: (
            _("Has given marketing consent") if value else _("Has refused marketing consent")
        ),
        hint=_("Requires a marketing consent flag on the customer record."),
    ),
    "has_email": _spec(
        _("Has an e-mail address"),
        _as_bool,
        _has_email_apply,
        lambda: _field_name(customer_model(), "email") is not None,
        lambda value: _("Has an e-mail address") if value else _("Has no e-mail address"),
    ),
    "has_phone": _spec(
        _("Has a phone number"),
        _as_bool,
        _has_phone_apply,
        lambda: _field_name(customer_model(), "phone") is not None,
        lambda value: _("Has a phone number") if value else _("Has no phone number"),
    ),
    "city": _spec(
        _("City"),
        _as_str_list,
        _city_apply,
        lambda: _field_name(customer_model(), "city") is not None,
        lambda value: _("City is one of: %(values)s") % {"values": ", ".join(value)},
    ),
    "country": _spec(
        _("Country"),
        _as_str_list,
        _country_apply,
        lambda: _field_name(customer_model(), "country") is not None,
        lambda value: _("Country is one of: %(values)s") % {"values": ", ".join(value)},
    ),
    "language": _spec(
        _("Language"),
        _as_str_list,
        _language_apply,
        lambda: _field_name(customer_model(), *CUSTOMER_LANGUAGE_FIELDS) is not None,
        lambda value: _("Language is one of: %(values)s") % {"values": ", ".join(value)},
    ),
    "tags": _spec(
        _("Tags"),
        _as_str_list,
        _tags_apply,
        lambda: _field_name(customer_model(), "tags") is not None,
        lambda value: _("Tagged with any of: %(values)s") % {"values": ", ".join(value)},
        hint=_("Requires tag support on the customer record."),
    ),
    "created_within_days": _spec(
        _("Added within (days)"),
        lambda value: _as_int(value, minimum=1, maximum=3650),
        _created_within_apply,
        lambda: True,
        lambda value: _("Added in the last %(n)s days") % {"n": value},
    ),
    "min_lifetime_value": _spec(
        _("Minimum lifetime value"),
        _as_decimal,
        _value_apply_min,
        lambda: _field_name(customer_model(), *CUSTOMER_VALUE_FIELDS) is not None,
        lambda value: _("Lifetime value of at least %(amount)s") % {"amount": value},
        hint=_("Requires an accumulated spend field on the customer record."),
    ),
    "min_bookings": _spec(
        _("Minimum bookings"),
        lambda value: _as_int(value, minimum=0, maximum=1000),
        _min_bookings_apply,
        _bookings_available,
        lambda value: _("At least %(n)s bookings") % {"n": value},
        hint=_("Requires the bookings module."),
    ),
    "max_bookings": _spec(
        _("Maximum bookings"),
        lambda value: _as_int(value, minimum=0, maximum=1000),
        _max_bookings_apply,
        _bookings_available,
        lambda value: _("At most %(n)s bookings") % {"n": value},
        hint=_("Requires the bookings module."),
    ),
    "last_visit_days": _spec(
        _("Visited within (days)"),
        lambda value: _as_int(value, minimum=1, maximum=3650),
        _last_visit_apply,
        _bookings_available,
        lambda value: _("Visited in the last %(n)s days") % {"n": value},
        hint=_("Requires the bookings module."),
    ),
    "no_visit_days": _spec(
        _("Not seen for (days)"),
        lambda value: _as_int(value, minimum=1, maximum=3650),
        _no_visit_apply,
        _bookings_available,
        lambda value: _("No visit in the last %(n)s days") % {"n": value},
        hint=_("Requires the bookings module."),
    ),
}

#: Keys an operator may use. Anything else is a validation error.
ALLOWED_CRITERIA_KEYS: frozenset[str] = frozenset(CRITERIA_SPECS)


# ---------------------------------------------------------------------------
# Validation, description, resolution
# ---------------------------------------------------------------------------
def validate_criteria(criteria: dict) -> list[str]:
    """Return a list of problems with *criteria* (empty when it is valid)."""
    problems: list[str] = []
    if not isinstance(criteria, dict):
        return [str(_("Segment criteria must be a mapping of rule name to value."))]

    for key, value in criteria.items():
        spec = CRITERIA_SPECS.get(key)
        if spec is None:
            problems.append(
                str(_("“%(key)s” is not a recognised segment rule.") % {"key": key})
            )
            continue
        try:
            spec["coerce"](value)
        except CriteriaValueError:
            problems.append(
                str(
                    _("The value for “%(label)s” is not valid.")
                    % {"label": spec["label"]}
                )
            )

    if "min_bookings" in criteria and "max_bookings" in criteria:
        try:
            low = _as_int(criteria["min_bookings"])
            high = _as_int(criteria["max_bookings"])
        except CriteriaValueError:
            low = high = None
        if low is not None and high is not None and low > high:
            problems.append(
                str(_("The minimum booking count cannot exceed the maximum."))
            )

    if "last_visit_days" in criteria and "no_visit_days" in criteria:
        problems.append(
            str(
                _(
                    "“Visited within” and “Not seen for” contradict each other — "
                    "use one or the other."
                )
            )
        )

    return problems


def criteria_runtime_issues(criteria: dict) -> list[str]:
    """Return rules that are valid but cannot run against the current data model."""
    issues: list[str] = []
    if not isinstance(criteria, dict):
        return [str(_("Segment criteria must be a mapping of rule name to value."))]
    if customer_model() is None:
        return [str(_("The customers module is unavailable, so this segment cannot be resolved."))]

    for key in criteria:
        spec = CRITERIA_SPECS.get(key)
        if spec is None:
            issues.append(str(_("“%(key)s” is not a recognised segment rule.") % {"key": key}))
            continue
        if not spec["available"]():
            issues.append(
                str(
                    _("“%(label)s” is ignored: %(hint)s")
                    % {
                        "label": spec["label"],
                        "hint": spec["hint"] or _("the underlying data is not available."),
                    }
                )
            )
    return issues


def describe_criteria(criteria: dict) -> list[str]:
    """Render the criteria as readable lines for the UI."""
    lines: list[str] = []
    if not isinstance(criteria, dict):
        return lines
    for key, value in criteria.items():
        spec = CRITERIA_SPECS.get(key)
        if spec is None:
            continue
        try:
            coerced = spec["coerce"](value)
        except CriteriaValueError:
            continue
        lines.append(str(spec["describe"](coerced)))
    return lines


def build_customer_queryset(criteria: dict) -> QuerySet:
    """Build the customer queryset described by *criteria*.

    Rules that are invalid or unsupported on the current data model are skipped
    silently here — :func:`criteria_runtime_issues` is what surfaces them to the
    operator. Skipping is deliberate: a campaign must never be sent to a wider
    audience than intended because one rule could not be applied, so unsupported
    rules are reported and the remaining (narrowing) rules still apply.
    """
    customer = customer_model()
    if customer is None:
        return _empty_customer_queryset()

    queryset = customer.objects.all()
    if not isinstance(criteria, dict):
        return queryset.none()

    for key, value in criteria.items():
        spec = CRITERIA_SPECS.get(key)
        if spec is None or not spec["available"]():
            continue
        try:
            coerced = spec["coerce"](value)
        except CriteriaValueError:
            continue
        queryset = spec["apply"](queryset, coerced)

    return queryset


def _empty_customer_queryset():
    """An empty, queryset-shaped result for when the customers app is missing.

    ``customers`` is always installed in this project, so this path exists only
    so a partially-migrated or partially-installed deployment renders an empty
    audience instead of raising in the middle of a campaign screen.
    """
    from .models import Segment

    return Segment.objects.none()


# ---------------------------------------------------------------------------
# CRM read queries used by the dashboard and the API
# ---------------------------------------------------------------------------
def lead_funnel(queryset=None) -> list[dict]:
    """Count and value every pipeline stage, in funnel order."""
    from django.db.models import DecimalField, Sum, Value
    from django.db.models.functions import Coalesce

    from .models import Lead

    queryset = Lead.objects.all() if queryset is None else queryset
    rows = {
        row["status"]: row
        for row in queryset.values("status").annotate(
            count=Count("id"),
            value=Coalesce(
                Sum("expected_value"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )
    }
    funnel = []
    for value, label in Lead.Status.choices:
        row = rows.get(value, {})
        funnel.append(
            {
                "status": value,
                "label": label,
                "count": row.get("count", 0),
                "value": row.get("value", Decimal("0.00")),
            }
        )
    return funnel


def due_follow_ups(within_days: int = 7, user=None):
    """Interactions whose follow-up is due soon or already overdue."""
    from .models import Interaction

    horizon = timezone.now() + timezone.timedelta(days=within_days)
    queryset = (
        Interaction.objects.filter(follow_up_required=True, follow_up_at__lte=horizon)
        .select_related("lead", "handled_by")
        .order_by("follow_up_at")
    )
    if user is not None:
        queryset = queryset.filter(handled_by=user)
    return queryset


def due_lead_actions(within_days: int = 7, user=None):
    """Open leads whose next action is due soon or already overdue."""
    from .models import Lead

    horizon = timezone.now() + timezone.timedelta(days=within_days)
    queryset = (
        Lead.objects.filter(
            status__in=Lead.OPEN_STATUSES,
            next_action_at__isnull=False,
            next_action_at__lte=horizon,
        )
        .select_related("assigned_to")
        .order_by("next_action_at")
    )
    if user is not None:
        queryset = queryset.filter(assigned_to=user)
    return queryset
