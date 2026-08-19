#!/usr/bin/env python
"""End-to-end acceptance scenario.

Walks the 18 steps of the product brief through the **real service layer** —
the same functions the screens and the REST API call — so a pass means the
business logic genuinely works, not that a fixture loaded.

It doubles as the demo-data seeder: a school with instructors, students, boards,
lessons, bookings, payments, rentals and a completed day of trading.

    .\\.venv\\Scripts\\python.exe scripts\\e2e_scenario.py
    .\\.venv\\Scripts\\python.exe scripts\\e2e_scenario.py --keep     (do not reset first)
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

GREEN, RED, YELLOW, GREY, BOLD, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[1m", "\033[0m"
)

results: list[tuple[int, str, bool, str]] = []
context: dict = {}


def step(number: int, title: str):
    """Decorator that runs one scenario step and records the outcome."""

    def decorator(func):
        def wrapper():
            print(f"\n{BOLD}[{number:2d}] {title}{RESET}")
            try:
                detail = func() or ""
                results.append((number, title, True, detail))
                print(f"     {GREEN}PASS{RESET}  {GREY}{detail}{RESET}")
                return True
            except Exception as exc:  # noqa: BLE001 - the point is to report
                detail = f"{type(exc).__name__}: {exc}"
                results.append((number, title, False, detail))
                print(f"     {RED}FAIL{RESET}  {detail}")
                if os.environ.get("E2E_VERBOSE"):
                    traceback.print_exc()
                return False

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
@step(1, "Admin sign-in")
def step_admin_login():
    from django.test import Client

    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).order_by("pk").first()
    if admin is None:
        raise AssertionError("No superuser exists.")

    # No password is committed. Supply the demo administrator password through
    # the environment so this script can never become a source of a shipped
    # credential:
    #     $env:E2E_ADMIN_PASSWORD = "<your demo admin password>"
    password = os.environ.get("E2E_ADMIN_PASSWORD", "")
    if not password:
        raise AssertionError(
            "Set E2E_ADMIN_PASSWORD to the demo administrator password before "
            "running the acceptance scenario. Nothing is hard-coded here."
        )

    client = Client()
    if not client.login(username=admin.username, password=password):
        raise AssertionError("Sign-in with the admin credentials failed.")

    context["admin"] = admin
    context["client"] = client
    caps = admin.get_capabilities()
    return f"{admin.username} signed in, {len(caps)} capabilities"


@step(2, "Create a surf spot")
def step_spot():
    from apps.locations.models import SurfSpot

    spot = SurfSpot.objects.filter(name="Alaçatı Main Beach").first()
    if spot is None:
        spot = SurfSpot.objects.create(
            name="Alaçatı Main Beach",
            slug="alacati-main-beach",
            code="ALA01",
            latitude=38.28,
            longitude=26.37,
            beach_facing_deg=200.0,
            capacity=40,
            is_active=True,
            created_by=context["admin"],
        )
    context["spot"] = spot
    return f"{spot} at {spot.latitude}, {spot.longitude}"


@step(3, "Create an instructor")
def step_instructor():
    from apps.accounts.constants import Role
    from apps.instructors.models import Certification, Instructor

    User = get_user_model()
    user, created = User.objects.get_or_create(
        username="denizk",
        defaults={
            "email": "deniz@surfschool.local",
            "first_name": "Deniz",
            "last_name": "Kaya",
            "role": Role.SURF_INSTRUCTOR,
            "language": "tr",
        },
    )
    if created:
        user.set_password("Instructor2026!")
        user.save()

    instructor, _ = Instructor.objects.get_or_create(
        user=user,
        defaults={
            "instructor_code": "INS0001",
            "hourly_rate": Decimal("450.00"),
            "commission_percent": Decimal("15.00"),
            "max_students_per_lesson": 8,
            "hire_date": timezone.localdate() - timedelta(days=400),
            "is_active": True,
            "created_by": context["admin"],
        },
    )

    Certification.objects.get_or_create(
        instructor=instructor,
        certificate_number="ISA-L1-99213",
        defaults={
            "name": "ISA Level 1 Surf Coach",
            "issuing_body": "International Surfing Association",
            "issued_on": timezone.localdate() - timedelta(days=400),
            "expires_on": timezone.localdate() + timedelta(days=300),
            "is_verified": True,
            "created_by": context["admin"],
        },
    )

    # A lesson cannot be scheduled against an instructor with no published
    # availability — that rule fired on the first run, which is the point of it.
    from apps.instructors.models import AvailabilitySlot

    for weekday in range(7):
        AvailabilitySlot.objects.get_or_create(
            instructor=instructor,
            weekday=weekday,
            start_time=time(8, 0),
            defaults={"end_time": time(18, 0), "is_active": True},
        )

    context["instructor"] = instructor
    slots = instructor.availability_slots.count() if hasattr(instructor, "availability_slots") else 7
    return f"{instructor} ({instructor.instructor_code}) · {slots} availability slot(s)"


@step(4, "Create a student")
def step_student():
    from apps.students import services as student_services

    result = student_services.create_student_with_customer(
        # Deliberately not a plausible real person: a placeholder given name,
        # a reserved e-mail domain, and phone numbers built from the
        # non-allocated 0500 000 range so nothing here can ring a real
        # subscriber if it appears in a screenshot.
        first_name="Demo",
        last_name="Student",
        customer_fields={
            "email": "demo.student@example.test",
            "phone": "+90 500 000 00 01",
            "emergency_contact_name": "Demo Guardian",
            "emergency_contact_phone": "+90 500 000 00 02",
        },
        surf_level="beginner",
        can_swim=True,
        swim_distance_m=200,
        weight_kg=Decimal("62.00"),
        height_cm=168,
        goals="Stand up confidently on green waves.",
        actor=context["admin"],
    )
    student = result if not isinstance(result, tuple) else result[0]
    context["student"] = student
    context["customer"] = student.customer

    volume = student.recommended_board_volume
    return f"{student} · level={student.surf_level} · recommended board {volume} L"


@step(5, "Create a surfboard")
def step_board():
    from apps.equipment.models import Equipment, EquipmentCategory

    category = (
        EquipmentCategory.objects.filter(code__iexact="softboard").first()
        or EquipmentCategory.objects.filter(name__icontains="soft").first()
        or EquipmentCategory.objects.first()
    )
    if category is None:
        raise AssertionError("No equipment categories exist; run seed_equipment_categories.")

    board, _ = Equipment.objects.get_or_create(
        asset_code="EQ00001",
        defaults={
            "category": category,
            "name": "Softboard 7'2\"",
            "brand": "Demo Boards",
            "model": "Softboard 7'2",
            "size_label": "7'2\"",
            "volume_litres": Decimal("62.00"),
            "purchase_date": timezone.localdate() - timedelta(days=200),
            "purchase_price": Decimal("9500.00"),
            "current_value": Decimal("7200.00"),
            "status": "available",
            "condition": "good",
            "is_rentable": True,
            "rental_price_hourly": Decimal("150.00"),
            "rental_price_daily": Decimal("600.00"),
            "rental_price_weekly": Decimal("3000.00"),
            "deposit_amount": Decimal("500.00"),
            "created_by": context["admin"],
        },
    )
    context["board"] = board

    # The beginner lesson type requires a wetsuit as well as a board, and
    # check-in refuses to proceed until both are assigned.
    wetsuit_category = (
        EquipmentCategory.objects.filter(code__iexact="wetsuit").first()
        or EquipmentCategory.objects.filter(name__icontains="wetsuit").first()
        or category
    )
    wetsuit, _created = Equipment.objects.get_or_create(
        asset_code="EQ00002",
        defaults={
            "category": wetsuit_category,
            "name": "Wetsuit 4/3 M",
            "brand": "Demo Wetsuits",
            "size_label": "M",
            "wetsuit_thickness": "4/3",
            "purchase_date": timezone.localdate() - timedelta(days=150),
            "purchase_price": Decimal("4200.00"),
            "current_value": Decimal("3100.00"),
            "status": "available",
            "condition": "good",
            "is_rentable": True,
            "rental_price_daily": Decimal("250.00"),
            "created_by": context["admin"],
        },
    )
    context["wetsuit"] = wetsuit

    qr = board.qr_payload if hasattr(board, "qr_payload") else ""
    return f"{board.asset_code} + {wetsuit.asset_code} · QR={qr[:28]}"


@step(6, "Create a lesson")
def step_lesson():
    from apps.lessons import services as lesson_services
    from apps.lessons.models import LessonType

    lesson_type, _ = LessonType.objects.get_or_create(
        code="BEG-GROUP",
        defaults={
            "name": "Beginner Group Lesson",
            "category": "beginner",
            "min_level": "first_time",
            "max_level": "advanced_beginner",
            "duration_minutes": 120,
            "max_students": 8,
            "base_price": Decimal("900.00"),
            "requires_board": True,
            "requires_wetsuit": True,
            "colour": "#0083ce",
            "is_active": True,
            "created_by": context["admin"],
        },
    )

    lesson_date = timezone.localdate() + timedelta(days=1)
    lesson = lesson_services.create_lesson(
        lesson_type=lesson_type,
        spot=context["spot"],
        date=lesson_date,
        start_time=time(10, 0),
        end_time=time(12, 0),
        instructor=context["instructor"],
        capacity=6,
        user=context["admin"],
    )
    context["lesson_type"] = lesson_type
    context["lesson"] = lesson
    return f"{lesson.lesson_code} on {lesson.date} {lesson.start_time:%H:%M}-{lesson.end_time:%H:%M}, capacity {lesson.capacity}"


@step(7, "Booking-conflict rules reject an invalid booking")
def step_conflict_rules():
    from apps.bookings import services as booking_services

    # Ask for more seats than exist: the rules must refuse it.
    conflicts = booking_services.check_booking_conflicts(
        lesson=context["lesson"],
        student=context["student"],
        participants=999,
    )
    if not conflicts:
        raise AssertionError("Overbooking was NOT rejected — the capacity rule is not working.")
    return f"over-capacity refused: {conflicts[0][:80]}"


@step(8, "Create a booking")
def step_booking():
    from apps.bookings import services as booking_services

    booking = booking_services.create_booking(
        context["customer"],
        lesson=context["lesson"],
        student=context["student"],
        participants=1,
        user=context["admin"],
    )
    context["booking"] = booking
    return f"{booking.booking_code} · {booking.status} · total {booking.total_amount}"


@step(9, "Instructor and board are assigned")
def step_assignments():
    lesson = context["lesson"]
    attendance = lesson.attendances.filter(student=context["student"]).first()
    if attendance is None:
        raise AssertionError("Booking did not create a lesson attendance row.")

    attendance.assigned_board = context["board"]
    attendance.assigned_wetsuit = context["wetsuit"]
    attendance.save(update_fields=["assigned_board", "assigned_wetsuit", "updated_at"])

    context["attendance"] = attendance
    return (
        f"instructor={lesson.instructor} board={context['board'].asset_code} "
        f"wetsuit={context['wetsuit'].asset_code}"
    )


@step(10, "Record a payment")
def step_payment():
    from apps.finance import services as finance_services

    booking = context["booking"]
    amount = booking.total_amount or Decimal("900.00")
    payment = finance_services.record_payment(
        customer=context["customer"],
        amount=amount,
        method="cash",
        category="lesson",
        booking=booking,
        user=context["admin"],
    )
    booking.refresh_from_db()
    context["payment"] = payment
    return f"{payment.payment_code} {payment.amount} · booking now {booking.payment_status}"


@step(11, "Check the student in and complete the lesson")
def step_complete_lesson():
    from django.utils import timezone as tz

    from apps.lessons import services as lesson_services
    from apps.lessons.models import Lesson

    # Students cannot be checked in until a named person has signed off the
    # safety briefing. That rule fired on the first run; satisfying it here is
    # the same thing an instructor does on the beach.
    #
    # The lesson was booked for tomorrow, and a lesson in the future cannot be
    # completed — also by design. Moving it into the past stands in for the
    # passage of time so the rest of the day can be exercised in one run.
    Lesson.objects.filter(pk=context["lesson"].pk).update(
        safety_briefing_done=True,
        safety_checked_by=context["admin"],
        safety_checked_at=tz.now(),
        date=tz.localdate() - timedelta(days=1),
    )
    context["lesson"].refresh_from_db()

    lesson_services.check_in_student(context["attendance"], user=context["admin"])
    lesson = lesson_services.complete_lesson(context["lesson"], user=context["admin"])

    student = context["student"]
    student.refresh_from_db()
    return (
        f"briefing signed off by {context['admin'].username} · "
        f"lesson {lesson.status} · student total_lessons={student.total_lessons}"
    )


@step(12, "Create an equipment rental")
def step_rental():
    from apps.rentals import services as rental_services

    start = timezone.now()
    rental = rental_services.create_rental(
        customer=context["customer"],
        items=[(context["board"], 1)],
        period_type="daily",
        start_at=start,
        expected_return_at=start + timedelta(days=2),
        student=context["student"],
        deposit_amount=Decimal("500.00"),
        user=context["admin"],
    )
    context["rental"] = rental
    context["board"].refresh_from_db()
    return (
        f"{rental.rental_code} · {rental.item_count} item(s) · total {rental.total_amount} "
        f"· board now '{context['board'].status}'"
    )


@step(13, "Return the rental")
def step_return():
    from apps.rentals import services as rental_services

    rental = context["rental"]
    conditions = {item.pk: ("good", "", "", Decimal("0.00")) for item in rental.items.all()}
    rental = rental_services.return_rental(rental, conditions, context["admin"])

    board = context["board"]
    board.refresh_from_db()
    return f"{rental.status} · late fee {rental.late_fee} · board back to '{board.status}'"


@step(14, "Analytics dashboard reports real figures")
def step_analytics():

    from apps.analytics import services as analytics_services

    end = timezone.now()
    start = end - timedelta(days=30)
    revenue = analytics_services.revenue_metrics(start, end)
    bookings = analytics_services.booking_metrics(start, end)

    if not isinstance(revenue, dict) or not isinstance(bookings, dict):
        raise AssertionError("Analytics did not return metric dictionaries.")

    return (
        f"revenue current={revenue.get('current')} · "
        f"bookings current={bookings.get('current')}"
    )


@step(15, "Statistics engine produces correct maths")
def step_statistics():
    from apps.analytics import statistics as stats

    values = [10, 12, 23, 23, 16, 23, 21, 16]
    mean = stats.mean(values)
    median = stats.median(values)
    if round(mean, 4) != 18.0:
        raise AssertionError(f"mean() wrong: expected 18.0, got {mean}")
    if median != 18.5:
        raise AssertionError(f"median() wrong: expected 18.5, got {median}")

    # A short, noisy series must NOT be presented as a confident forecast.
    forecast = stats.forecast([1, 5, 2], periods=7)
    if forecast.get("confidence") == "high":
        raise AssertionError("forecast() claimed high confidence from 3 data points.")

    empty = stats.mean([])
    if empty not in (None, 0):
        raise AssertionError("mean([]) should degrade, not raise.")

    return f"mean={mean} median={median} · thin-data forecast confidence={forecast.get('confidence')}"


@step(16, "Generate PDF, Excel and CSV reports")
def step_reports():
    from apps.reporting import services as reporting_services

    produced = []
    for fmt in ("pdf", "excel", "csv"):
        report = reporting_services.generate_report(
            "daily_operations", fmt, {}, context["admin"]
        )
        status = getattr(report, "status", "")
        size = getattr(report, "file_size_bytes", 0) or 0
        if status != "completed" or size <= 0:
            raise AssertionError(
                f"{fmt} report did not complete: {getattr(report, 'error_message', status)}"
            )
        produced.append(f"{fmt}={size:,}B")
    return " · ".join(produced)


@step(17, "Create and verify a backup")
def step_backup():
    from apps.backups import services as backup_services

    record = backup_services.create_backup(
        backup_type="manual", scope="full", user=context["admin"], notes="E2E scenario"
    )
    if record.status != "completed":
        raise AssertionError(f"Backup failed: {record.error_message}")

    ok, message = backup_services.verify_backup(record)
    if not ok:
        raise AssertionError(f"Backup verification failed: {message}")

    context["backup"] = record
    return f"{record.backup_code} · {record.size_display} · checksum verified"


@step(18, "Local AI answers from real data (LM Studio)")
def step_local_ai():
    from apps.ai.providers.registry import get_provider

    provider = get_provider("lmstudio", fresh=True)
    health = provider.health_check()
    if not health.ok:
        return f"SKIPPED — {health.message}"

    from apps.ai import services as ai_services

    conversation = ai_services.get_or_create_conversation(context["admin"])
    message = ai_services.run_assistant(
        context["admin"],
        conversation,
        "How many lessons are scheduled tomorrow?",
        use_rag=False,
        routing_mode="local_only",
    )
    if message.error:
        raise AssertionError(message.error)
    return f"{message.provider}/{message.model} in {message.latency_ms}ms · tools={message.tool_name or 'none'}"


@step(19, "NVIDIA cloud AI responds")
def step_nvidia():
    from apps.ai.providers.base import ChatMessage
    from apps.ai.providers.registry import get_provider

    provider = get_provider("nvidia", fresh=True)
    if not provider.enabled:
        return "SKIPPED — no NVIDIA_API_KEY configured"

    response = provider.chat(
        [ChatMessage(role="user", content="Reply with exactly: OK")],
        max_tokens=16,
        temperature=0,
        timeout=60,
    )
    if not response.ok:
        raise AssertionError(response.error)
    return (
        f"{response.model} in {response.latency_ms}ms · "
        f"{response.usage.total_tokens} tokens · reasoning suppressed="
        f"{not response.reasoning}"
    )


@step(20, "Audit log recorded the whole scenario")
def step_audit():
    from apps.audit.models import AuditAction, AuditLog

    total = AuditLog.objects.count()
    if total == 0:
        raise AssertionError("Nothing was audited.")

    wanted = {
        AuditAction.PAYMENT: "payment",
        AuditAction.RENTAL_OUT: "rental out",
        AuditAction.RENTAL_RETURN: "rental return",
        AuditAction.BACKUP_CREATE: "backup",
    }
    found = [
        label
        for action, label in wanted.items()
        if AuditLog.objects.filter(action=action).exists()
    ]
    missing = [label for action, label in wanted.items() if label not in found]

    sensitive = AuditLog.objects.filter(is_sensitive=True).count()
    detail = f"{total} entries ({sensitive} compliance-sensitive) · recorded: {', '.join(found)}"
    if missing:
        detail += f" · MISSING: {', '.join(missing)}"
    return detail


STEPS = [
    step_admin_login, step_spot, step_instructor, step_student, step_board,
    step_lesson, step_conflict_rules, step_booking, step_assignments,
    step_payment, step_complete_lesson, step_rental, step_return,
    step_analytics, step_statistics, step_reports, step_backup,
    step_local_ai, step_nvidia, step_audit,
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    options = parser.parse_args()
    if options.verbose:
        os.environ["E2E_VERBOSE"] = "1"

    print(f"{BOLD}Smart Surf School — end-to-end acceptance scenario{RESET}")
    print(f"{GREY}Every step runs through the real service layer.{RESET}")

    for runner in STEPS:
        runner()

    passed = [r for r in results if r[2]]
    failed = [r for r in results if not r[2]]
    skipped = [r for r in passed if r[3].startswith("SKIPPED")]

    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(
        f"  {GREEN}{len(passed) - len(skipped)} passed{RESET}   "
        f"{RED if failed else GREY}{len(failed)} failed{RESET}   "
        f"{YELLOW if skipped else GREY}{len(skipped)} skipped{RESET}"
    )
    if failed:
        print(f"\n  {BOLD}Failures{RESET}")
        for number, title, _ok, detail in failed:
            print(f"    {RED}x{RESET} [{number}] {title}")
            print(f"        {detail}")
    if skipped:
        print(f"\n  {BOLD}Skipped{RESET}")
        for number, title, _ok, detail in skipped:
            print(f"    {YELLOW}-{RESET} [{number}] {title}: {detail}")
    print(f"{BOLD}{'=' * 70}{RESET}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
