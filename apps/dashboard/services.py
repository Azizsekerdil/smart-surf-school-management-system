"""Dashboard composition rules.

The dashboard answers one question — *what needs a person right now?* — and it
answers it differently depending on who is asking:

``build_staff_dashboard``       the operations view: the whole school's day.
``build_instructor_dashboard``  leads with the instructor's own lessons.
``build_customer_dashboard``    a self-service view of one person's bookings.

:func:`build_dashboard_context` picks the right builder from the signed-in
user's role, and every tile inside a builder is guarded by the capability that
would let the user open the underlying screen. A rental clerk has no
``finance.view``, so the revenue query is never issued for them — not merely
hidden in the template.

Numbers are never invented. A tile whose source module is absent carries
``value = None``, which the template renders as a dash with an explanation.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.enums import (
    MAX_WIND_KMH,
    WAVE_HEIGHT_SUITABILITY,
    LessonStatus,
    SurfLevel,
    TideState,
    WindType,
    recommended_wetsuit,
    wind_type_from_directions,
)

from . import selectors

#: The three level bands the conditions panel scores. First-timers share the
#: beginner band and competition surfers the advanced one, which is how a
#: briefing is actually given on the beach.
PANEL_LEVELS: tuple[str, ...] = (
    SurfLevel.BEGINNER,
    SurfLevel.INTERMEDIATE,
    SurfLevel.ADVANCED,
)

#: Days of takings shown in the sparkline.
REVENUE_WINDOW_DAYS = 14

#: Weighting between wave quality and wind quality in the suitability score.
WAVE_WEIGHT = 0.65
WIND_WEIGHT = 0.35


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _can(user, capability: str) -> bool:
    return bool(user and user.is_authenticated and user.has_capability(capability))


def _can_any(user, *capabilities: str) -> bool:
    return any(_can(user, capability) for capability in capabilities)


def module_links() -> dict[str, str | None]:
    """Deep links into the other modules, resolved defensively.

    A link that cannot be reversed becomes ``None`` and the template renders
    plain text instead of a dead anchor.
    """
    reverse = selectors.safe_reverse
    return {
        "lessons_day": reverse("lessons:day"),
        "lessons_list": reverse("lessons:list"),
        "bookings_calendar": reverse("bookings:calendar"),
        "bookings_list": reverse("bookings:list"),
        "bookings_create": reverse("bookings:create"),
        "rentals_list": reverse("rentals:list"),
        "rentals_out": reverse("rentals:out_now"),
        "rentals_create": reverse("rentals:create"),
        "equipment_list": reverse("equipment:list"),
        "maintenance_list": reverse("maintenance:list"),
        "instructors_list": reverse("instructors:list"),
        "instructors_availability": reverse("instructors:availability_board"),
        "customers_list": reverse("customers:list"),
        "students_list": reverse("students:list"),
        "finance_dashboard": reverse("finance:dashboard"),
        "surf_conditions": reverse("surf_conditions:dashboard"),
        "audit_list": reverse("audit:list"),
        "ai_chat": reverse("ai:chat"),
        "notifications_list": reverse("notifications:list"),
    }


def _tile(
    key: str,
    label,
    *,
    icon: str,
    value=None,
    kind: str = "count",
    detail: str = "",
    tone: str = "default",
    url: str | None = None,
    note: str = "",
) -> dict:
    """One stat tile.

    ``value=None`` means "this school has no source for that number yet";
    ``note`` explains it in the tile so nobody reads a dash as a zero.
    """
    return {
        "key": key,
        "label": label,
        "icon": icon,
        "value": value,
        # Templates cannot distinguish "0" from "unknown" on their own, and the
        # difference matters: one is a fact, the other is a missing module.
        "has_value": value is not None,
        "kind": kind,
        "detail": detail,
        "tone": tone,
        "url": url,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Surf suitability scoring
# ---------------------------------------------------------------------------
def level_suitability_score(
    level: str, wave_height_m: float | None, wind_kmh: float | None
) -> dict | None:
    """Score today's water 0–100 for one surf level.

    The bands come from :data:`apps.core.enums.WAVE_HEIGHT_SUITABILITY` and
    :data:`apps.core.enums.MAX_WIND_KMH`, which are the governing-body figures
    recorded in the domain research — this function only interpolates between
    them, it does not invent new thresholds.

    Above the level's ``max_safe`` wave height, or at or beyond its wind
    ceiling, the score is 0 and ``safe`` is ``False``: that is a hard stop, not
    a low rating.
    """
    bounds = WAVE_HEIGHT_SUITABILITY.get(level)
    if bounds is None or wave_height_m is None:
        return None

    minimum, ideal_low, ideal_high, max_safe = bounds
    height = float(wave_height_m)

    if height > max_safe:
        wave_score, wave_safe = 0.0, False
    elif height < minimum:
        wave_score = 40.0 * (height / minimum) if minimum > 0 else 0.0
        wave_safe = True
    elif height < ideal_low:
        span = ideal_low - minimum
        wave_score = 40.0 + 60.0 * ((height - minimum) / span if span > 0 else 1.0)
        wave_safe = True
    elif height <= ideal_high:
        wave_score, wave_safe = 100.0, True
    else:
        span = max_safe - ideal_high
        wave_score = 100.0 - 80.0 * ((height - ideal_high) / span if span > 0 else 1.0)
        wave_safe = True

    ceiling = MAX_WIND_KMH.get(level)
    wind_safe = True
    if wind_kmh is None or not ceiling:
        combined = wave_score
    else:
        speed = float(wind_kmh)
        if speed >= ceiling:
            wind_score, wind_safe = 0.0, False
        else:
            wind_score = 100.0 * (1.0 - speed / ceiling)
        combined = WAVE_WEIGHT * wave_score + WIND_WEIGHT * wind_score

    safe = wave_safe and wind_safe
    score = 0 if not safe else int(round(max(0.0, min(100.0, combined))))

    if not wave_safe:
        reason = _("Waves above the safe ceiling for this level.")
    elif not wind_safe:
        reason = _("Wind above the safe ceiling for this level.")
    elif score >= 70:
        reason = _("Good conditions.")
    elif score >= 40:
        reason = _("Workable, with a careful briefing.")
    else:
        reason = _("Marginal — consider postponing.")

    return {"score": score, "safe": safe, "reason": reason}


def surf_conditions_panel(spot=None) -> dict:
    """Live conditions plus a per-level score for the beach briefing."""
    spot = spot or selectors.primary_spot()
    reading = selectors.latest_surf_reading(spot)
    labels = dict(SurfLevel.choices)

    if reading is None:
        return {
            "available": False,
            "spot": spot,
            "levels": [],
            "url": selectors.safe_reverse("surf_conditions:dashboard"),
        }

    module_scores = reading.get("module_scores") or {}
    levels = []
    for value in PANEL_LEVELS:
        computed = level_suitability_score(
            value, reading["wave_height_m"], reading["wind_speed_kmh"]
        )
        published = module_scores.get(value)
        if published is not None:
            # The surf-conditions module owns the safety gate; its verdict wins.
            score = max(0, min(100, published["score"]))
            safe = published["safe"]
            reason = published["recommendation"] or (computed["reason"] if computed else "")
            source = "module"
        elif computed is not None:
            score, safe, reason, source = (
                computed["score"],
                computed["safe"],
                computed["reason"],
                "computed",
            )
        else:
            score, safe, reason, source = None, True, "", "none"

        levels.append(
            {
                "value": value,
                "label": labels.get(value, value),
                "score": score,
                "safe": safe,
                "reason": reason,
                "source": source,
                "tone": _score_tone(score, safe),
            }
        )

    wind_type = reading.get("wind_type")
    if not wind_type:
        facing = getattr(reading.get("spot") or spot, "beach_facing_deg", None)
        if reading["wind_direction_deg"] is not None and facing is not None:
            wind_type = wind_type_from_directions(reading["wind_direction_deg"], facing)

    return {
        "available": True,
        "spot": reading.get("spot") or spot,
        "observed_at": reading["observed_at"],
        "is_forecast": reading.get("is_forecast", False),
        "is_stale": reading.get("is_stale", False),
        "wave_height_m": reading["wave_height_m"],
        "wave_period_s": reading["wave_period_s"],
        "wind_speed_kmh": reading["wind_speed_kmh"],
        "wind_type": wind_type,
        "wind_type_label": dict(WindType.choices).get(wind_type),
        "water_temp_c": reading["water_temp_c"],
        "wetsuit": recommended_wetsuit(reading["water_temp_c"]),
        "tide_state": reading["tide_state"],
        "tide_label": dict(TideState.choices).get(reading["tide_state"]),
        "levels": levels,
        "url": selectors.safe_reverse("surf_conditions:dashboard"),
    }


def _score_tone(score: int | None, safe: bool) -> str:
    if score is None:
        return "default"
    if not safe:
        return "danger"
    if score >= 70:
        return "ok"
    if score >= 40:
        return "warning"
    return "danger"


# ---------------------------------------------------------------------------
# Today's schedule
# ---------------------------------------------------------------------------
def todays_schedule(day: date_cls, *, instructor=None) -> list[dict] | None:
    """Today's lessons as timeline rows, in start-time order."""
    lessons = selectors.todays_lessons(day, instructor=instructor)
    if lessons is None:
        return None

    now = timezone.localtime()
    rows: list[dict] = []
    for lesson in lessons:
        capacity = int(lesson.capacity or 0)
        booked = int(getattr(lesson, "booked", 0) or 0)
        awaiting = int(getattr(lesson, "awaiting_check_in", 0) or 0)
        running = (
            day == now.date()
            and lesson.start_time <= now.time() <= lesson.end_time
            and lesson.status in (LessonStatus.CONFIRMED, LessonStatus.IN_PROGRESS, LessonStatus.SCHEDULED)
        )
        finished = day < now.date() or (day == now.date() and lesson.end_time < now.time())
        rows.append(
            {
                "lesson": lesson,
                "booked": booked,
                "capacity": capacity,
                "fill_percent": int(round(100 * booked / capacity)) if capacity else 0,
                "awaiting_check_in": awaiting,
                "checked_in": int(getattr(lesson, "checked_in_count", 0) or 0),
                "is_running": running,
                "is_finished": finished,
                "is_full": capacity > 0 and booked >= capacity,
                "url": selectors.safe_reverse("lessons:detail", lesson.pk),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------
def shared_reads(user, day: date_cls) -> dict:
    """Values that more than one tile or panel needs, fetched exactly once.

    The dashboard is the busiest page in the product, so anything read twice is
    paid for twice on every load. Each entry is only present when the user's
    capabilities allow the underlying query at all — a missing key means "not
    permitted", a ``None`` value means "module absent".
    """
    shared: dict = {}

    if _can(user, "lessons.view"):
        shared["schedule"] = todays_schedule(day)
    if _can(user, "surf_conditions.view"):
        shared["surf"] = surf_conditions_panel()
    if _can_any(user, "equipment.view", "maintenance.view"):
        shared["equipment_warnings"] = selectors.equipment_warning_counts(day)
    if _can(user, "finance.view"):
        chart = revenue_sparkline(day)
        shared["revenue_chart"] = chart
        # The chart's last bucket *is* today's takings, computed from the same
        # ledger — reading it again would risk two different numbers on one page.
        shared["revenue_today"] = chart["today_total"] if chart else None
        shared["outstanding_booking"] = selectors.outstanding_booking_balance()
        shared["outstanding_rental"] = selectors.outstanding_rental_balance()

    return shared


def build_tiles(user, day: date_cls, *, shared: dict | None = None) -> list[dict]:
    """The stat tiles this user is allowed to see, in reading order.

    Each block runs its queries only after the capability check passes, so an
    unauthorised tile costs nothing. Pass ``shared`` (see :func:`shared_reads`)
    when the caller has already loaded the values the panels need too.
    """
    shared = shared if shared is not None else {}
    links = module_links()
    tiles: list[dict] = []

    # --- today's lessons -------------------------------------------------
    if _can(user, "lessons.view"):
        rows = shared["schedule"] if "schedule" in shared else todays_schedule(day)
        if rows is None:
            tiles.append(
                _tile(
                    "todays_lessons",
                    _("Today's lessons"),
                    icon="book-open",
                    note=_("The lessons module is not available."),
                )
            )
        else:
            running = sum(1 for row in rows if row["lesson"].status == LessonStatus.IN_PROGRESS)
            cancelled = sum(1 for row in rows if row["lesson"].status == LessonStatus.CANCELLED)
            detail = _("%(n)s in progress") % {"n": running} if running else ""
            if cancelled:
                detail = (
                    _("%(n)s cancelled") % {"n": cancelled}
                    if not detail
                    else _("%(detail)s · %(n)s cancelled") % {"detail": detail, "n": cancelled}
                )
            tiles.append(
                _tile(
                    "todays_lessons",
                    _("Today's lessons"),
                    icon="book-open",
                    value=len(rows),
                    detail=detail,
                    url=links["lessons_day"],
                )
            )

    # --- students in the water -------------------------------------------
    if _can(user, "lessons.view"):
        count = selectors.todays_student_count(day)
        tiles.append(
            _tile(
                "todays_students",
                _("Today's students"),
                icon="graduation-cap",
                value=count,
                detail=_("Holding a seat today") if count is not None else "",
                url=links["lessons_day"],
                note="" if count is not None else _("The lessons module is not available."),
            )
        )

    # --- revenue -----------------------------------------------------------
    if _can(user, "finance.view"):
        revenue = (
            shared["revenue_today"]
            if "revenue_today" in shared
            else selectors.revenue_between(day, day)
        )
        tiles.append(
            _tile(
                "todays_revenue",
                _("Today's revenue"),
                icon="banknote",
                value=revenue,
                kind="money",
                detail=_("Payments received today") if revenue is not None else "",
                url=links["finance_dashboard"],
                note="" if revenue is not None else _("No payment ledger yet."),
            )
        )

    # --- rentals -----------------------------------------------------------
    if _can(user, "rentals.view"):
        active = selectors.active_rental_count()
        due_today = selectors.rentals_due_back_today(day)
        detail = ""
        if active is not None and due_today:
            detail = _("%(n)s due back today") % {"n": due_today}
        tiles.append(
            _tile(
                "active_rentals",
                _("Active rentals"),
                icon="arrow-left-right",
                value=active,
                detail=detail,
                url=links["rentals_out"] or links["rentals_list"],
                note="" if active is not None else _("The rentals module is not available."),
            )
        )

    # --- equipment ---------------------------------------------------------
    if _can_any(user, "equipment.view", "maintenance.view"):
        warnings = (
            shared["equipment_warnings"]
            if "equipment_warnings" in shared
            else selectors.equipment_warning_counts(day)
        )
        if warnings is None:
            tiles.append(
                _tile(
                    "equipment_warnings",
                    _("Equipment warnings"),
                    icon="wrench",
                    note=_("The equipment module is not available."),
                )
            )
        else:
            tone = "default"
            if warnings["critical_repairs"] or warnings["damaged"]:
                tone = "danger"
            elif warnings["total"]:
                tone = "warning"
            tiles.append(
                _tile(
                    "equipment_warnings",
                    _("Equipment warnings"),
                    icon="wrench",
                    value=warnings["total"],
                    detail=_("%(service)s due a service · %(repairs)s open repairs")
                    % {"service": warnings["service_due"], "repairs": warnings["open_repairs"]},
                    tone=tone,
                    url=links["maintenance_list"] or links["equipment_list"],
                )
            )

    # --- surf conditions ---------------------------------------------------
    if _can(user, "surf_conditions.view"):
        panel = shared["surf"] if "surf" in shared else surf_conditions_panel()
        if not panel or not panel["available"]:
            tiles.append(
                _tile(
                    "surf_conditions",
                    _("Surf conditions"),
                    icon="waves",
                    url=(panel or {}).get("url"),
                    note=_("No reading recorded yet."),
                )
            )
        else:
            beginner = next((row for row in panel["levels"] if row["value"] == SurfLevel.BEGINNER), None)
            tiles.append(
                _tile(
                    "surf_conditions",
                    _("Surf conditions"),
                    icon="waves",
                    value=beginner["score"] if beginner else None,
                    kind="score",
                    detail=_("Beginner score at %(spot)s") % {"spot": panel["spot"]}
                    if panel["spot"]
                    else _("Beginner score"),
                    tone=beginner["tone"] if beginner else "default",
                    url=panel["url"],
                )
            )

    # --- instructors -------------------------------------------------------
    if _can(user, "instructors.view"):
        availability = selectors.instructor_availability(day)
        if availability is None:
            tiles.append(
                _tile(
                    "instructor_availability",
                    _("Instructor availability"),
                    icon="user-check",
                    note=_("The instructors module is not available."),
                )
            )
        else:
            tiles.append(
                _tile(
                    "instructor_availability",
                    _("Instructor availability"),
                    icon="user-check",
                    value=availability["available"],
                    kind="ratio",
                    detail=_("of %(total)s active · %(teaching)s teaching · %(leave)s on leave")
                    % {
                        "total": availability["active"],
                        "teaching": availability["teaching"],
                        "leave": availability["on_leave"],
                    },
                    tone="warning" if availability["active"] and not availability["available"] else "default",
                    url=links["instructors_availability"] or links["instructors_list"],
                )
            )

    # --- pending payments --------------------------------------------------
    if _can(user, "finance.view"):
        booking_balance = (
            shared["outstanding_booking"]
            if "outstanding_booking" in shared
            else selectors.outstanding_booking_balance()
        )
        rental_balance = (
            shared["outstanding_rental"]
            if "outstanding_rental" in shared
            else selectors.outstanding_rental_balance()
        )
        parts = [value for value in (booking_balance, rental_balance) if value is not None]
        total = sum(parts, Decimal("0.00")) if parts else None
        tiles.append(
            _tile(
                "pending_payments",
                _("Pending payments"),
                icon="receipt",
                value=total,
                kind="money",
                detail=_("Unpaid balances on bookings and rentals") if total is not None else "",
                tone="warning" if total and total > 0 else "default",
                url=links["bookings_list"],
                note="" if total is not None else _("No bookings or rentals module yet."),
            )
        )

    # --- AI alerts ---------------------------------------------------------
    if _can(user, "ai.view"):
        count = selectors.ai_alert_count(user)
        tiles.append(
            _tile(
                "ai_alerts",
                _("AI alerts"),
                icon="sparkles",
                value=count,
                detail=_("Unread recommendations awaiting your review") if count else "",
                tone="warning" if count else "default",
                url=links["notifications_list"],
                note="" if count is not None else _("The notifications module is not available."),
            )
        )

    return tiles


# ---------------------------------------------------------------------------
# Revenue sparkline
# ---------------------------------------------------------------------------
def revenue_sparkline(day: date_cls, *, days: int = REVENUE_WINDOW_DAYS) -> dict | None:
    """Daily takings for the last *days* days, ready for Chart.js.

    ``None`` when there is no payment ledger to read; the card then shows an
    empty state rather than a flat line at zero, which would read as "we took
    nothing" instead of "we do not know".
    """
    start = day - timedelta(days=days - 1)
    series = selectors.revenue_by_day(start, day)
    if series is None:
        return None
    total = sum((amount for _date, amount in series), Decimal("0.00"))
    previous_start = start - timedelta(days=days)
    previous_total = selectors.revenue_between(previous_start, start - timedelta(days=1))
    return {
        "labels": [entry_date.strftime("%d.%m") for entry_date, _amount in series],
        "values": [float(amount) for _date, amount in series],
        "total": total,
        # The last bucket is *day* itself, kept as an exact Decimal so the
        # revenue tile and this chart can never disagree.
        "today_total": series[-1][1] if series else Decimal("0.00"),
        "previous_total": previous_total,
        "days": days,
    }


# ---------------------------------------------------------------------------
# Panels shared by the staff and instructor dashboards
# ---------------------------------------------------------------------------
def _operational_panels(user, day: date_cls, shared: dict) -> dict:
    """The right-hand column: conditions, equipment, rentals, money, activity."""
    panels: dict = {"surf": shared.get("surf")}

    if _can_any(user, "equipment.view", "maintenance.view"):
        panels["equipment_warnings"] = shared.get("equipment_warnings")
        panels["open_repairs"] = selectors.open_maintenance_records()
        panels["service_due"] = selectors.equipment_service_due(day)

    if _can(user, "rentals.view"):
        panels["overdue_rentals"] = selectors.overdue_rentals()

    if _can(user, "finance.view"):
        panels["unpaid_bookings"] = selectors.bookings_awaiting_payment()
        panels["outstanding_total"] = shared.get("outstanding_booking")
        panels["overdue_invoices"] = selectors.overdue_invoice_count()

    if _can(user, "instructors.view"):
        panels["expiring_certifications"] = selectors.expiring_certifications()

    if _can(user, "audit.view"):
        panels["recent_activity"] = selectors.recent_activity()

    if _can(user, "ai.view"):
        panels["ai_alerts"] = selectors.ai_alerts(user)

    return panels


# ---------------------------------------------------------------------------
# Role-specific builders
# ---------------------------------------------------------------------------
def build_staff_dashboard(user, day: date_cls) -> dict:
    """The whole school's day, filtered by what this member of staff may see."""
    shared = shared_reads(user, day)

    context = {
        "dashboard_variant": "staff",
        "today": day,
        "tiles": build_tiles(user, day, shared=shared),
        "links": module_links(),
        "schedule": shared.get("schedule"),
        "schedule_title": _("Today's schedule"),
        "revenue_chart": shared.get("revenue_chart"),
        "pending_confirmations": selectors.pending_confirmation_count()
        if _can(user, "bookings.view")
        else None,
    }
    context.update(_operational_panels(user, day, shared))
    return context


def build_instructor_dashboard(user, day: date_cls, instructor) -> dict:
    """An instructor's day: their own lessons first, then the shared panels."""
    shared = shared_reads(user, day)
    school_schedule = shared.get("schedule")
    my_schedule = todays_schedule(day, instructor=instructor)

    context = {
        "dashboard_variant": "instructor",
        "today": day,
        "instructor": instructor,
        # Tiles keep counting the whole school — an instructor still needs to
        # know how busy the beach is — while the timeline below is theirs.
        "tiles": build_tiles(user, day, shared=shared),
        "links": module_links(),
        "schedule": my_schedule,
        "schedule_title": _("My lessons today"),
        "my_student_count": sum(row["booked"] for row in my_schedule) if my_schedule else 0,
        "my_awaiting_check_in": sum(row["awaiting_check_in"] for row in my_schedule)
        if my_schedule
        else 0,
        "school_lesson_count": len(school_schedule) if school_schedule else 0,
        "revenue_chart": shared.get("revenue_chart"),
        "pending_confirmations": selectors.pending_confirmation_count()
        if _can(user, "bookings.view")
        else None,
    }
    context.update(_operational_panels(user, day, shared))
    return context


def build_customer_dashboard(user, day: date_cls) -> dict:
    """What one customer or student may see: their own bookings and nothing else."""
    links = module_links()
    customer = selectors.customer_for_user(user)
    student = selectors.student_for_customer(customer)

    context: dict = {
        "dashboard_variant": "customer",
        "today": day,
        "links": links,
        "customer": customer,
        "student": student,
        "tiles": [],
        "schedule": None,
        "schedule_title": _("My bookings"),
        "profile_missing": customer is None,
    }

    if customer is None:
        # A self-service account with no customer record yet: say so plainly
        # rather than rendering an empty dashboard that looks broken.
        context["surf"] = surf_conditions_panel() if _can(user, "surf_conditions.view") else None
        return context

    upcoming_queryset = selectors.bookings_for_customer(customer, upcoming_from=day)
    upcoming = list(upcoming_queryset[:10]) if upcoming_queryset is not None else None
    upcoming_count = upcoming_queryset.count() if upcoming_queryset is not None else None
    past = selectors.bookings_for_customer(customer, before=day, limit=5)
    balance = (
        selectors.outstanding_booking_balance(customer=customer)
        if _can(user, "finance.view")
        else None
    )

    tiles = [
        _tile(
            "my_upcoming",
            _("Upcoming bookings"),
            icon="calendar-days",
            value=upcoming_count,
            url=links["bookings_list"],
            note="" if upcoming_count is not None else _("The bookings module is not available."),
        )
    ]
    if _can(user, "rentals.view"):
        rental_queryset = selectors.rentals_for_customer(customer)
        rentals = list(rental_queryset[:5]) if rental_queryset is not None else None
        rental_count = rental_queryset.count() if rental_queryset is not None else None
        context["my_rentals"] = rentals
        tiles.append(
            _tile(
                "my_rentals",
                _("My rentals"),
                icon="arrow-left-right",
                value=rental_count,
                url=links["rentals_list"],
                note="" if rental_count is not None else _("The rentals module is not available."),
            )
        )
    if _can(user, "finance.view"):
        tiles.append(
            _tile(
                "my_balance",
                _("Balance due"),
                icon="receipt",
                value=balance,
                kind="money",
                tone="warning" if balance and balance > 0 else "default",
                note="" if balance is not None else _("The bookings module is not available."),
            )
        )

    context.update(
        {
            "tiles": tiles,
            "upcoming_bookings": upcoming,
            "recent_bookings": past,
            "balance_due": balance,
            "surf": surf_conditions_panel() if _can(user, "surf_conditions.view") else None,
        }
    )
    return context


def build_dashboard_context(user, today: date_cls | None = None) -> dict:
    """Return the dashboard context for *user* on *today*.

    The role decides the shape: external accounts (customers, students) never
    reach a builder that queries school-wide data, and an instructor with a
    linked profile gets their own lessons at the top.
    """
    day = today or timezone.localdate()

    if getattr(user, "is_external", False):
        return build_customer_dashboard(user, day)

    instructor = selectors.instructor_for_user(user)
    if instructor is not None:
        return build_instructor_dashboard(user, day, instructor)

    return build_staff_dashboard(user, day)


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------
def _rows(items, builder) -> list[dict]:
    return [builder(item) for item in items] if items is not None else []


def global_search(user, term: str) -> dict:
    """Search every module the user may view, grouped by record type.

    External users are scoped to their own customer record before any query
    runs, so a customer cannot enumerate the school's clientele through the
    search box.
    """
    term = (term or "").strip()
    result: dict = {
        "term": term,
        "too_short": 0 < len(term) < selectors.MIN_SEARCH_LENGTH,
        "groups": [],
        "total": 0,
        "limit": selectors.SEARCH_GROUP_LIMIT,
    }
    if len(term) < selectors.MIN_SEARCH_LENGTH:
        return result

    external = bool(getattr(user, "is_external", False))
    scope = selectors.customer_for_user(user) if external else None
    if external and scope is None:
        # No customer record: there is nothing this account may legitimately
        # find, so no query is issued at all.
        return result

    groups: list[dict] = []
    reverse = selectors.safe_reverse

    if _can(user, "customers.view"):
        items = selectors.search_customers(term, scope_to=scope)
        groups.append(
            {
                "key": "customers",
                "label": _("Customers"),
                "icon": "users",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": obj.full_name,
                        "subtitle": obj.customer_code or obj.email,
                        "meta": obj.phone,
                        "url": reverse("customers:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "students.view"):
        items = selectors.search_students(term, scope_to=scope)
        groups.append(
            {
                "key": "students",
                "label": _("Students"),
                "icon": "graduation-cap",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": obj.full_name,
                        "subtitle": obj.student_code,
                        "meta": obj.get_surf_level_display(),
                        "url": reverse("students:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "instructors.view") and not external:
        items = selectors.search_instructors(term)
        groups.append(
            {
                "key": "instructors",
                "label": _("Instructors"),
                "icon": "user-check",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": obj.full_name,
                        "subtitle": obj.instructor_code,
                        "meta": obj.get_max_level_taught_display(),
                        "url": reverse("instructors:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "bookings.view"):
        items = selectors.search_bookings(term, scope_to=scope)
        groups.append(
            {
                "key": "bookings",
                "label": _("Bookings"),
                "icon": "calendar-days",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": obj.booking_code,
                        "subtitle": str(obj.customer),
                        "meta": obj.get_status_display(),
                        "url": reverse("bookings:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "lessons.view") and not external:
        items = selectors.search_lessons(term)
        groups.append(
            {
                "key": "lessons",
                "label": _("Lessons"),
                "icon": "book-open",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": f"{obj.lesson_code} · {obj.lesson_type.name}",
                        "subtitle": f"{obj.date:%d.%m.%Y} {obj.time_label}",
                        "meta": str(obj.spot),
                        "url": reverse("lessons:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "equipment.view") and not external:
        items = selectors.search_equipment(term)
        groups.append(
            {
                "key": "equipment",
                "label": _("Equipment"),
                "icon": "package",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": f"{obj.asset_code} · {obj.name}",
                        "subtitle": obj.specification_summary,
                        "meta": obj.get_status_display(),
                        "url": reverse("equipment:detail", obj.pk),
                    },
                ),
            }
        )

    if _can(user, "rentals.view"):
        items = selectors.search_rentals(term, scope_to=scope)
        groups.append(
            {
                "key": "rentals",
                "label": _("Rentals"),
                "icon": "arrow-left-right",
                "rows": _rows(
                    items,
                    lambda obj: {
                        "title": obj.rental_code,
                        "subtitle": str(obj.customer),
                        "meta": obj.get_status_display(),
                        "url": reverse("rentals:detail", obj.pk),
                    },
                ),
            }
        )

    populated = [group for group in groups if group["rows"]]
    result["groups"] = populated
    result["total"] = sum(len(group["rows"]) for group in populated)
    return result
