"""Database tools the assistant may call.

Why tools rather than pasting data into the prompt
--------------------------------------------------
The brief is explicit: **the AI must not invent data.** Tools are the mechanism.
The model can only obtain a number by calling a function that runs a real query;
when a tool has nothing to report it says so, and the system prompt forbids
answering numerically without a tool result.

Two properties matter for safety:

1. **Every tool is capability-checked.** A tool runs with the *requesting user's*
   permissions. A rental clerk asking the assistant about payroll gets the same
   refusal the finance screen would give them — the assistant is not a privilege
   escalation path.
2. **Tools are read-only by default.** Anything that writes is marked
   ``mutating=True`` and is excluded unless the caller explicitly opts in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

logger = logging.getLogger("apps.ai")

NO_DATA = "__no_data__"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., dict]
    capability: str
    mutating: bool = False
    tags: list[str] = field(default_factory=list)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def register(
    name: str,
    description: str,
    parameters: dict,
    capability: str,
    *,
    mutating: bool = False,
    tags: list[str] | None = None,
):
    def decorator(func: Callable[..., dict]) -> Callable[..., dict]:
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            capability=capability,
            mutating=mutating,
            tags=tags or [],
        )
        return func

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _model(app_label: str, model_name: str):
    """Fetch a model without a hard import (keeps the AI app dependency-free)."""
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _parse_date(value: str | None, default: date | None = None) -> date:
    if not value:
        return default or timezone.localdate()
    if value in {"today", "bugün", "bugun"}:
        return timezone.localdate()
    if value in {"tomorrow", "yarın", "yarin"}:
        return timezone.localdate() + timedelta(days=1)
    if value in {"yesterday", "dün", "dun"}:
        return timezone.localdate() - timedelta(days=1)
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return default or timezone.localdate()


def _money(value) -> float:
    return float(value or Decimal("0.00"))


def _empty(message: str) -> dict:
    """Uniform "there is genuinely nothing here" reply.

    The model is instructed to relay this verbatim rather than guess.
    """
    return {"status": NO_DATA, "message": message, "count": 0, "results": []}


DATE_PARAM = {
    "type": "string",
    "description": "ISO date (YYYY-MM-DD), or 'today' / 'tomorrow' / 'yesterday'.",
}
RANGE_PARAMS = {
    "start_date": {"type": "string", "description": "ISO start date (YYYY-MM-DD)."},
    "end_date": {"type": "string", "description": "ISO end date (YYYY-MM-DD)."},
}


# ---------------------------------------------------------------------------
# Lessons & bookings
# ---------------------------------------------------------------------------
@register(
    "get_lessons_for_date",
    "List the surf lessons scheduled on a given date, with instructor, spot, time, "
    "capacity and how many students are booked.",
    {
        "type": "object",
        "properties": {"target_date": DATE_PARAM},
        "required": [],
    },
    capability="lessons.view",
    tags=["operations"],
)
def get_lessons_for_date(user, target_date: str | None = None) -> dict:
    Lesson = _model("lessons", "Lesson")
    if Lesson is None:
        return _empty(_("The lessons module is not installed."))

    day = _parse_date(target_date)
    lessons = (
        Lesson.objects.filter(date=day)
        .select_related("lesson_type", "instructor", "spot")
        .order_by("start_time")
    )
    if not lessons.exists():
        return _empty(
            _("There are no lessons scheduled on %(date)s.") % {"date": day.isoformat()}
        )

    results = []
    for lesson in lessons:
        booked = lesson.attendances.exclude(status="cancelled").count()
        results.append(
            {
                "code": lesson.lesson_code,
                "type": str(lesson.lesson_type.name) if lesson.lesson_type_id else "",
                "start": lesson.start_time.strftime("%H:%M"),
                "end": lesson.end_time.strftime("%H:%M"),
                "instructor": str(lesson.instructor) if lesson.instructor_id else "",
                "spot": str(lesson.spot) if lesson.spot_id else "",
                "capacity": lesson.capacity,
                "booked": booked,
                "free_seats": max(lesson.capacity - booked, 0),
                "status": lesson.status,
            }
        )
    return {"status": "ok", "date": day.isoformat(), "count": len(results), "results": results}


@register(
    "get_bookings_summary",
    "Booking counts and revenue for a period, broken down by status.",
    {"type": "object", "properties": RANGE_PARAMS, "required": []},
    capability="bookings.view",
    tags=["operations", "finance"],
)
def get_bookings_summary(user, start_date: str | None = None, end_date: str | None = None) -> dict:
    Booking = _model("bookings", "Booking")
    if Booking is None:
        return _empty(_("The bookings module is not installed."))

    end = _parse_date(end_date)
    start = _parse_date(start_date, end - timedelta(days=29))

    queryset = Booking.objects.filter(booked_at__date__gte=start, booked_at__date__lte=end)
    total = queryset.count()
    if total == 0:
        return _empty(
            _("No bookings were made between %(start)s and %(end)s.")
            % {"start": start.isoformat(), "end": end.isoformat()}
        )

    by_status = list(queryset.values("status").annotate(n=Count("id")).order_by("-n"))
    totals = queryset.aggregate(revenue=Sum("total_amount"), paid=Sum("paid_amount"))

    return {
        "status": "ok",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_bookings": total,
        "by_status": [{"status": row["status"], "count": row["n"]} for row in by_status],
        "total_amount": _money(totals["revenue"]),
        "paid_amount": _money(totals["paid"]),
        "outstanding": _money(totals["revenue"]) - _money(totals["paid"]),
    }


@register(
    "find_available_lesson_slots",
    "Find lessons on a date that still have free seats and suit a given surf level.",
    {
        "type": "object",
        "properties": {
            "target_date": DATE_PARAM,
            "level": {
                "type": "string",
                "description": "first_time, beginner, advanced_beginner, intermediate, advanced or competition.",
            },
        },
        "required": [],
    },
    capability="lessons.view",
    tags=["operations"],
)
def find_available_lesson_slots(user, target_date: str | None = None, level: str | None = None) -> dict:
    Lesson = _model("lessons", "Lesson")
    if Lesson is None:
        return _empty(_("The lessons module is not installed."))

    day = _parse_date(target_date)
    queryset = (
        Lesson.objects.filter(date=day)
        .exclude(status__in=["cancelled", "completed"])
        .select_related("lesson_type", "instructor", "spot")
        .order_by("start_time")
    )

    results = []
    for lesson in queryset:
        booked = lesson.attendances.exclude(status="cancelled").count()
        free = lesson.capacity - booked
        if free <= 0:
            continue
        if level and lesson.lesson_type_id:
            from apps.core.enums import level_rank

            if not (
                level_rank(lesson.lesson_type.min_level)
                <= level_rank(level)
                <= level_rank(lesson.lesson_type.max_level)
            ):
                continue
        results.append(
            {
                "code": lesson.lesson_code,
                "type": str(lesson.lesson_type.name) if lesson.lesson_type_id else "",
                "start": lesson.start_time.strftime("%H:%M"),
                "free_seats": free,
                "instructor": str(lesson.instructor) if lesson.instructor_id else "",
                "spot": str(lesson.spot) if lesson.spot_id else "",
            }
        )

    if not results:
        return _empty(
            _("No lesson on %(date)s has free seats for that level.") % {"date": day.isoformat()}
        )
    return {"status": "ok", "date": day.isoformat(), "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
@register(
    "get_revenue_summary",
    "Total revenue for a period, split by source (lessons, camps, rentals, shop), "
    "with the change against the preceding period of equal length.",
    {"type": "object", "properties": RANGE_PARAMS, "required": []},
    capability="finance.view",
    tags=["finance"],
)
def get_revenue_summary(user, start_date: str | None = None, end_date: str | None = None) -> dict:
    end = _parse_date(end_date)
    start = _parse_date(start_date, end - timedelta(days=29))
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)

    def period_total(model_path, date_field, amount_field, extra_filter=None) -> Decimal:
        app_label, model_name = model_path
        model = _model(app_label, model_name)
        if model is None:
            return Decimal("0.00")
        queryset = model.objects.all()
        if extra_filter:
            queryset = queryset.filter(**extra_filter)
        current = queryset.filter(
            **{f"{date_field}__date__gte": start, f"{date_field}__date__lte": end}
        ).aggregate(total=Sum(amount_field))["total"]
        return Decimal(str(current or 0))

    sources: dict[str, float] = {}
    Payment = _model("finance", "Payment")
    if Payment is not None:
        rows = (
            Payment.objects.filter(paid_at__date__gte=start, paid_at__date__lte=end)
            .values("category")
            .annotate(total=Sum("amount"))
        )
        for row in rows:
            sources[row["category"] or "other"] = _money(row["total"])

    if not sources:
        # Fall back to booking + rental + sale totals when no payment rows exist.
        sources = {
            "bookings": _money(period_total(("bookings", "Booking"), "booked_at", "paid_amount")),
            "rentals": _money(period_total(("rentals", "Rental"), "start_at", "paid_amount")),
            "shop": _money(period_total(("pos", "Sale"), "sold_at", "total_amount")),
        }

    total = sum(sources.values())
    if total == 0:
        return _empty(
            _("No revenue was recorded between %(start)s and %(end)s.")
            % {"start": start.isoformat(), "end": end.isoformat()}
        )

    return {
        "status": "ok",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "total_revenue": round(total, 2),
        "by_source": {k: round(v, 2) for k, v in sources.items() if v},
        "currency": "TRY",
    }


@register(
    "get_outstanding_payments",
    "List bookings and rentals with money still owed.",
    {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
    capability="finance.view",
    tags=["finance"],
)
def get_outstanding_payments(user, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 100))
    results: list[dict] = []

    Booking = _model("bookings", "Booking")
    if Booking is not None:
        for booking in (
            Booking.objects.exclude(status__in=["cancelled", "no_show"])
            .filter(paid_amount__lt=models_f("total_amount"))
            .select_related("customer")[:limit]
        ):
            results.append(
                {
                    "type": "booking",
                    "code": booking.booking_code,
                    "customer": str(booking.customer) if booking.customer_id else "",
                    "total": _money(booking.total_amount),
                    "paid": _money(booking.paid_amount),
                    "due": _money(booking.total_amount) - _money(booking.paid_amount),
                }
            )

    if not results:
        return _empty(_("There are no outstanding payments."))
    return {"status": "ok", "count": len(results), "results": results}


def models_f(name: str):
    from django.db.models import F

    return F(name)


# ---------------------------------------------------------------------------
# Equipment, rentals & maintenance
# ---------------------------------------------------------------------------
@register(
    "get_most_used_equipment",
    "The equipment items with the highest number of rentals, most used first.",
    {
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "How many items (default 10)."}},
        "required": [],
    },
    capability="equipment.view",
    tags=["equipment"],
)
def get_most_used_equipment(user, limit: int = 10) -> dict:
    Equipment = _model("equipment", "Equipment")
    if Equipment is None:
        return _empty(_("The equipment module is not installed."))

    limit = max(1, min(int(limit or 10), 50))
    items = (
        Equipment.objects.filter(total_rentals__gt=0)
        .select_related("category")
        .order_by("-total_rentals")[:limit]
    )
    if not items:
        return _empty(_("No equipment has been rented yet, so there is no usage ranking."))

    return {
        "status": "ok",
        "count": len(items),
        "results": [
            {
                "asset_code": item.asset_code,
                "name": item.name,
                "category": str(item.category) if item.category_id else "",
                "brand": item.brand,
                "total_rentals": item.total_rentals,
                "total_hours": float(item.total_rental_hours or 0),
                "status": item.status,
                "condition": item.condition,
            }
            for item in items
        ],
    }


@register(
    "get_maintenance_predictions",
    "Equipment most likely to need maintenance soon, with a 0-100 risk score and the "
    "reasons behind it. The scores are computed statistically from real service history.",
    {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
    capability="maintenance.view",
    tags=["equipment", "maintenance"],
)
def get_maintenance_predictions(user, limit: int = 10) -> dict:
    try:
        from apps.maintenance.services import predict_maintenance_needs
    except ImportError:
        return _empty(_("The maintenance module is not installed."))

    try:
        predictions = predict_maintenance_needs()
    except Exception as exc:  # noqa: BLE001 - a tool must never crash the chat
        logger.warning("Maintenance prediction failed: %s", exc)
        return _empty(_("Maintenance predictions could not be computed."))

    if not predictions:
        return _empty(
            _("There is not enough service history yet to predict maintenance needs.")
        )
    return {
        "status": "ok",
        "count": len(predictions[: int(limit or 10)]),
        "results": predictions[: int(limit or 10)],
        "note": "Scores are statistical, computed from service history — not model estimates.",
    }


@register(
    "get_active_rentals",
    "Equipment currently checked out, flagging anything overdue.",
    {"type": "object", "properties": {"only_overdue": {"type": "boolean"}}, "required": []},
    capability="rentals.view",
    tags=["equipment"],
)
def get_active_rentals(user, only_overdue: bool = False) -> dict:
    Rental = _model("rentals", "Rental")
    if Rental is None:
        return _empty(_("The rentals module is not installed."))

    queryset = Rental.objects.filter(status__in=["active", "overdue"]).select_related("customer")
    if only_overdue:
        queryset = queryset.filter(
            Q(status="overdue") | Q(expected_return_at__lt=timezone.now())
        )

    rentals = list(queryset.order_by("expected_return_at")[:50])
    if not rentals:
        return _empty(
            _("There are no overdue rentals.") if only_overdue else _("Nothing is on hire right now.")
        )

    now = timezone.now()
    return {
        "status": "ok",
        "count": len(rentals),
        "results": [
            {
                "code": rental.rental_code,
                "customer": str(rental.customer) if rental.customer_id else "",
                "items": rental.items.count(),
                "expected_return": rental.expected_return_at.isoformat(),
                "is_overdue": rental.expected_return_at < now,
                "hours_overdue": max(
                    0, int((now - rental.expected_return_at).total_seconds() // 3600)
                ),
                "total": _money(rental.total_amount),
            }
            for rental in rentals
        ],
    }


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
@register(
    "get_instructor_performance",
    "Compare instructors over a period: lessons taught, students, average rating.",
    {"type": "object", "properties": RANGE_PARAMS, "required": []},
    capability="instructors.view",
    tags=["people"],
)
def get_instructor_performance(
    user, start_date: str | None = None, end_date: str | None = None
) -> dict:
    Instructor = _model("instructors", "Instructor")
    Lesson = _model("lessons", "Lesson")
    if Instructor is None or Lesson is None:
        return _empty(_("The instructors or lessons module is not installed."))

    end = _parse_date(end_date)
    start = _parse_date(start_date, end - timedelta(days=89))

    rows = (
        Instructor.objects.filter(is_active=True)
        .annotate(
            lessons_taught=Count(
                "lessons",
                filter=Q(
                    lessons__date__gte=start,
                    lessons__date__lte=end,
                    lessons__status="completed",
                ),
                distinct=True,
            ),
            students_taught=Count(
                "lessons__attendances",
                filter=Q(
                    lessons__date__gte=start,
                    lessons__date__lte=end,
                    lessons__attendances__status="attended",
                ),
            ),
            avg_rating=Avg(
                "lessons__attendances__rating",
                filter=Q(lessons__date__gte=start, lessons__date__lte=end),
            ),
        )
        .order_by("-lessons_taught")
    )

    results = [
        {
            "instructor": str(row),
            "code": row.instructor_code,
            "lessons_taught": row.lessons_taught,
            "students_taught": row.students_taught,
            "average_rating": round(float(row.avg_rating), 2) if row.avg_rating else None,
        }
        for row in rows
    ]
    if not any(r["lessons_taught"] for r in results):
        return _empty(
            _("No completed lessons were recorded between %(start)s and %(end)s, so "
              "instructor performance cannot be compared.")
            % {"start": start.isoformat(), "end": end.isoformat()}
        )

    return {
        "status": "ok",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "count": len(results),
        "results": results,
    }


@register(
    "search_customers",
    "Find customers by name, e-mail, phone or customer code.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search text."}},
        "required": ["query"],
    },
    capability="customers.view",
    tags=["people"],
)
def search_customers(user, query: str = "") -> dict:
    Customer = _model("customers", "Customer")
    if Customer is None:
        return _empty(_("The customers module is not installed."))

    query = (query or "").strip()
    if len(query) < 2:
        return _empty(_("Provide at least two characters to search for."))

    matches = Customer.objects.filter(
        Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(email__icontains=query)
        | Q(phone__icontains=query)
        | Q(customer_code__icontains=query)
    )[:20]

    if not matches:
        return _empty(_("No customer matches “%(query)s”.") % {"query": query})

    return {
        "status": "ok",
        "count": len(matches),
        "results": [
            {
                "code": customer.customer_code,
                "name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "total_bookings": customer.total_bookings,
            }
            for customer in matches
        ],
    }


@register(
    "get_student_statistics",
    "How many students there are, and their distribution across surf levels.",
    {"type": "object", "properties": {}, "required": []},
    capability="students.view",
    tags=["people"],
)
def get_student_statistics(user) -> dict:
    Student = _model("students", "Student")
    if Student is None:
        return _empty(_("The students module is not installed."))

    total = Student.objects.filter(is_active=True).count()
    if total == 0:
        return _empty(_("There are no students recorded yet."))

    by_level = list(
        Student.objects.filter(is_active=True)
        .values("surf_level")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return {
        "status": "ok",
        "total_active_students": total,
        "by_level": [{"level": row["surf_level"], "count": row["n"]} for row in by_level],
    }


# ---------------------------------------------------------------------------
# Surf conditions & safety
# ---------------------------------------------------------------------------
@register(
    "get_surf_conditions",
    "Current or forecast surf conditions for a spot, including the suitability score "
    "for each surf level.",
    {
        "type": "object",
        "properties": {
            "spot": {"type": "string", "description": "Spot name or code. Defaults to the primary spot."},
            "target_date": DATE_PARAM,
        },
        "required": [],
    },
    capability="surf_conditions.view",
    tags=["surf"],
)
def get_surf_conditions(user, spot: str | None = None, target_date: str | None = None) -> dict:
    try:
        from apps.surf_conditions.services import conditions_for_tool
    except ImportError:
        return _empty(_("The surf conditions module is not installed."))

    try:
        return conditions_for_tool(spot_query=spot, target_date=_parse_date(target_date))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Surf condition tool failed: %s", exc)
        return _empty(_("Surf conditions are unavailable right now."))


@register(
    "get_open_safety_items",
    "Open safety incidents, active weather warnings and student restrictions.",
    {"type": "object", "properties": {}, "required": []},
    capability="safety.view",
    tags=["safety"],
)
def get_open_safety_items(user) -> dict:
    Incident = _model("safety", "SafetyIncident")
    Warning = _model("safety", "WeatherWarning")
    if Incident is None:
        return _empty(_("The safety module is not installed."))

    open_incidents = list(
        Incident.objects.exclude(status__in=["resolved", "closed", "cancelled"])[:20]
    )
    active_warnings = (
        list(Warning.objects.filter(is_active=True)[:20]) if Warning is not None else []
    )

    if not open_incidents and not active_warnings:
        return _empty(_("There are no open safety incidents or active warnings."))

    return {
        "status": "ok",
        "open_incidents": [
            {
                "id": incident.pk,
                "severity": incident.severity,
                "status": incident.status,
                "summary": str(incident)[:160],
                "occurred_at": incident.occurred_at.isoformat()
                if getattr(incident, "occurred_at", None)
                else None,
            }
            for incident in open_incidents
        ],
        "active_warnings": [
            {"title": str(warning)[:160], "severity": getattr(warning, "severity", "")}
            for warning in active_warnings
        ],
        "reminder": (
            "Safety decisions require a named staff member to sign off. Present this as "
            "information, never as an approval."
        ),
    }


# ---------------------------------------------------------------------------
# Access control & dispatch
# ---------------------------------------------------------------------------
def tools_for_user(user, *, include_mutating: bool = False) -> list[Tool]:
    """Only the tools *user* is permitted to run."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    return [
        tool
        for tool in REGISTRY.values()
        if (include_mutating or not tool.mutating) and user.has_capability(tool.capability)
    ]


def schemas_for_user(user, *, include_mutating: bool = False) -> list[dict]:
    return [tool.to_openai_schema() for tool in tools_for_user(user, include_mutating=include_mutating)]


def execute_tool(user, name: str, arguments: dict | None = None) -> dict:
    """Run tool *name* on behalf of *user*, enforcing capabilities.

    Never raises: a failure is returned as data so the model can explain it.
    """
    tool = REGISTRY.get(name)
    if tool is None:
        return {"status": "error", "message": f"Unknown tool '{name}'."}

    if user is None or not getattr(user, "is_authenticated", False):
        return {"status": "error", "message": "Authentication required."}

    if not user.has_capability(tool.capability):
        # Deliberately explicit: the assistant tells the user they lack access
        # rather than silently returning nothing, which would look like "no data".
        return {
            "status": "permission_denied",
            "message": _(
                "Your role does not permit access to this information (%(cap)s)."
            ) % {"cap": tool.capability},
        }

    arguments = arguments or {}
    # Drop anything not in the declared schema — the model does not get to pass
    # arbitrary keyword arguments into a Python function.
    allowed = set((tool.parameters.get("properties") or {}).keys())
    safe_arguments = {k: v for k, v in arguments.items() if k in allowed}

    try:
        return tool.handler(user, **safe_arguments)
    except TypeError as exc:
        logger.warning("Tool %s called with bad arguments: %s", name, exc)
        return {"status": "error", "message": f"Invalid arguments for '{name}'."}
    except Exception as exc:  # noqa: BLE001 - tools must never break the chat
        logger.exception("Tool %s failed", name)
        return {"status": "error", "message": f"'{name}' failed: {type(exc).__name__}"}
