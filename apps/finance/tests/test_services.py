"""Service tests: the money rules that a real school depends on."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.constants import Role
from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import LessonStatus, PaymentMethod, PaymentStatus
from apps.customers.tests.factories import CustomerFactory
from apps.finance import selectors, services
from apps.finance.models import CommissionRecord, CustomerPackage, Invoice, Payment
from apps.instructors.tests.factories import InstructorFactory

from .factories import (
    CustomerPackageFactory,
    ExpenseCategoryFactory,
    ExpenseFactory,
    InvoiceFactory,
    InvoiceLineFactory,
    PaymentFactory,
    PricePackageFactory,
    build_booking,
    build_lesson,
    build_rental,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def finance_user(db):
    return User.objects.create_user(
        username="treasurer",
        email="treasurer@example.test",
        password="pw-test-12345",
        role=Role.FINANCE,
    )


@pytest.fixture
def reception_user(db):
    """Reception may take money but may not refund it."""
    return User.objects.create_user(
        username="frontdesk",
        email="frontdesk@example.test",
        password="pw-test-12345",
        role=Role.RECEPTION,
    )


def _issued_invoice(customer=None, amount=Decimal("750.00")) -> Invoice:
    invoice = InvoiceFactory(customer=customer or CustomerFactory())
    InvoiceLineFactory(invoice=invoice, unit_price=amount, quantity=Decimal("1.00"))
    invoice.recalculate()
    invoice.status = Invoice.Status.ISSUED
    invoice.save(update_fields=["status"])
    return invoice


# ---------------------------------------------------------------------------
# record_payment
# ---------------------------------------------------------------------------
def test_record_payment_settles_the_invoice(finance_user):
    invoice = _issued_invoice()
    payment = services.record_payment(
        invoice.customer,
        Decimal("750.00"),
        method=PaymentMethod.CARD,
        category=Payment.Category.LESSON,
        invoice=invoice,
        user=finance_user,
    )

    invoice.refresh_from_db()
    assert payment.amount == Decimal("750.00")
    assert invoice.paid_amount == Decimal("750.00")
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance_due == Decimal("0.00")


def test_a_part_payment_leaves_the_invoice_partial(finance_user):
    invoice = _issued_invoice()
    services.record_payment(
        invoice.customer, Decimal("250.00"), invoice=invoice, user=finance_user
    )

    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("250.00")
    assert invoice.status == Invoice.Status.PARTIAL
    assert invoice.balance_due == Decimal("500.00")


def test_two_payments_accumulate_without_a_lost_update(finance_user):
    invoice = _issued_invoice()
    services.record_payment(invoice.customer, Decimal("300.00"), invoice=invoice, user=finance_user)
    services.record_payment(invoice.customer, Decimal("450.00"), invoice=invoice, user=finance_user)

    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("750.00")
    assert invoice.status == Invoice.Status.PAID


def test_record_payment_updates_the_booking_balance(finance_user):
    booking = build_booking(unit_price=Decimal("750.00"))
    services.record_payment(
        booking.customer,
        Decimal("750.00"),
        category=Payment.Category.LESSON,
        booking=booking,
        user=finance_user,
    )

    booking.refresh_from_db()
    assert booking.paid_amount == Decimal("750.00")
    assert booking.payment_status == PaymentStatus.PAID


def test_record_payment_updates_the_rental_balance(finance_user):
    rental = build_rental(subtotal=Decimal("300.00"))
    services.record_payment(
        rental.customer,
        Decimal("150.00"),
        category=Payment.Category.RENTAL,
        rental=rental,
        user=finance_user,
    )

    rental.refresh_from_db()
    assert rental.paid_amount == Decimal("150.00")
    assert rental.payment_status == PaymentStatus.PARTIAL


def test_record_payment_writes_a_payment_audit_entry(finance_user):
    customer = CustomerFactory()
    services.record_payment(customer, Decimal("120.00"), user=finance_user)

    entry = AuditLog.objects.filter(action=AuditAction.PAYMENT).first()
    assert entry is not None
    assert entry.is_sensitive is True


@pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-10.00")])
def test_record_payment_refuses_a_non_positive_amount(finance_user, amount):
    with pytest.raises(ValidationError):
        services.record_payment(CustomerFactory(), amount, user=finance_user)


def test_record_payment_needs_a_customer(finance_user):
    with pytest.raises(ValidationError):
        services.record_payment(None, Decimal("10.00"), user=finance_user)


# ---------------------------------------------------------------------------
# refund_payment
# ---------------------------------------------------------------------------
def test_refund_writes_a_negative_counterpart_and_leaves_the_original(finance_user):
    invoice = _issued_invoice()
    payment = services.record_payment(
        invoice.customer, Decimal("750.00"), invoice=invoice, user=finance_user
    )

    refund = services.refund_payment(
        payment, Decimal("250.00"), "Lesson cut short by the wind.", user=finance_user
    )
    payment.refresh_from_db()
    invoice.refresh_from_db()

    assert refund.amount == Decimal("-250.00")
    assert refund.is_refund is True
    assert refund.refunded_payment_id == payment.pk
    # The original is untouched — history is never rewritten.
    assert payment.amount == Decimal("750.00")
    assert payment.is_refund is False
    assert invoice.paid_amount == Decimal("500.00")


def test_a_refund_nets_out_of_the_revenue_figures(finance_user):
    customer = CustomerFactory()
    payment = services.record_payment(customer, Decimal("400.00"), user=finance_user)
    services.refund_payment(payment, Decimal("100.00"), "Damaged board.", user=finance_user)

    assert selectors.gross_revenue() == Decimal("400.00")
    assert selectors.refunds_total() == Decimal("100.00")
    assert selectors.net_revenue() == Decimal("300.00")


def test_refund_cannot_exceed_what_is_left(finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    services.refund_payment(payment, Decimal("60.00"), "Partly cancelled.", user=finance_user)
    payment.refresh_from_db()

    with pytest.raises(ValidationError):
        services.refund_payment(payment, Decimal("50.00"), "Rest of it.", user=finance_user)


def test_refund_requires_the_refund_capability(reception_user, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    with pytest.raises(PermissionDenied):
        services.refund_payment(payment, Decimal("10.00"), "Nope.", user=reception_user)
    assert Payment.objects.filter(is_refund=True).count() == 0


def test_refund_needs_a_reason(finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    with pytest.raises(ValidationError):
        services.refund_payment(payment, Decimal("10.00"), "   ", user=finance_user)


def test_refund_writes_a_refund_audit_entry(finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    services.refund_payment(payment, Decimal("10.00"), "Goodwill.", user=finance_user)

    assert AuditLog.objects.filter(action=AuditAction.REFUND).exists()


def test_a_refund_row_cannot_be_refunded_again(finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    refund = services.refund_payment(payment, Decimal("100.00"), "All of it.", user=finance_user)
    with pytest.raises(ValidationError):
        services.refund_payment(refund, Decimal("10.00"), "Again.", user=finance_user)


# ---------------------------------------------------------------------------
# Invoices from operational records
# ---------------------------------------------------------------------------
def test_create_invoice_for_booking_bills_the_seats(finance_user):
    booking = build_booking(unit_price=Decimal("750.00"), participants=2)
    invoice = services.create_invoice_for_booking(booking, user=finance_user)

    assert invoice.customer_id == booking.customer_id
    assert invoice.booking_id == booking.pk
    assert invoice.total_amount == Decimal("1500.00")
    assert invoice.lines.count() == 1


def test_create_invoice_for_booking_is_idempotent(finance_user):
    booking = build_booking()
    first = services.create_invoice_for_booking(booking, user=finance_user)
    second = services.create_invoice_for_booking(booking, user=finance_user)
    assert first.pk == second.pk
    assert Invoice.objects.filter(booking=booking).count() == 1


def test_an_invoice_carries_money_already_taken_on_the_booking(finance_user):
    booking = build_booking(unit_price=Decimal("750.00"))
    services.record_payment(
        booking.customer, Decimal("250.00"), booking=booking, user=finance_user
    )
    booking.refresh_from_db()

    invoice = services.create_invoice_for_booking(booking, user=finance_user)
    assert invoice.paid_amount == Decimal("250.00")
    assert invoice.balance_due == Decimal("500.00")


def test_create_invoice_for_rental_includes_late_and_damage_fees(finance_user):
    rental = build_rental(
        subtotal=Decimal("300.00"), late_fee=Decimal("50.00"), damage_fee=Decimal("120.00")
    )
    invoice = services.create_invoice_for_rental(rental, user=finance_user)

    assert invoice.lines.count() == 3
    assert invoice.total_amount == Decimal("470.00")


def test_issue_invoice_moves_a_draft_to_issued(finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"))
    invoice.recalculate()

    services.issue_invoice(invoice, user=finance_user)
    assert invoice.status == Invoice.Status.ISSUED


def test_an_empty_invoice_cannot_be_issued(finance_user):
    invoice = InvoiceFactory()
    with pytest.raises(ValidationError):
        services.issue_invoice(invoice, user=finance_user)


def test_a_paid_invoice_cannot_be_cancelled(finance_user):
    invoice = _issued_invoice()
    services.record_payment(
        invoice.customer, Decimal("750.00"), invoice=invoice, user=finance_user
    )
    invoice.refresh_from_db()
    with pytest.raises(ValidationError):
        services.cancel_invoice(invoice, "Mistake.", user=finance_user)


def test_overdue_invoices_lists_only_unpaid_past_due_rows(finance_user):
    overdue = _issued_invoice()
    overdue.due_date = timezone.localdate() - timedelta(days=5)
    overdue.save(update_fields=["due_date"])
    _issued_invoice()  # inside terms

    codes = [invoice.invoice_number for invoice in services.overdue_invoices()]
    assert codes == [overdue.invoice_number]


def test_mark_overdue_invoices_flips_the_status(finance_user):
    invoice = _issued_invoice()
    invoice.due_date = timezone.localdate() - timedelta(days=2)
    invoice.save(update_fields=["due_date"])

    assert services.mark_overdue_invoices() == 1
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.OVERDUE


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
def test_calculate_commission_uses_the_booked_value_of_completed_lessons(finance_user):
    instructor = InstructorFactory(commission_percent=Decimal("10.00"))
    lesson = build_lesson(instructor=instructor, status=LessonStatus.COMPLETED)
    build_booking(lesson=lesson, unit_price=Decimal("750.00"), participants=2)

    records = services.calculate_commission(
        instructor,
        timezone.localdate() - timedelta(days=7),
        timezone.localdate(),
        user=finance_user,
    )

    assert len(records) == 1
    assert records[0].base_amount == Decimal("1500.00")
    assert records[0].commission_amount == Decimal("150.00")
    assert records[0].status == CommissionRecord.Status.PENDING


def test_calculating_twice_never_pays_twice(finance_user):
    instructor = InstructorFactory(commission_percent=Decimal("10.00"))
    lesson = build_lesson(instructor=instructor, status=LessonStatus.COMPLETED)
    build_booking(lesson=lesson, unit_price=Decimal("750.00"))

    window = (timezone.localdate() - timedelta(days=7), timezone.localdate())
    services.calculate_commission(instructor, *window, user=finance_user)
    again = services.calculate_commission(instructor, *window, user=finance_user)

    assert again == []
    assert CommissionRecord.objects.filter(instructor=instructor).count() == 1


def test_no_commission_for_a_lesson_that_never_ran(finance_user):
    instructor = InstructorFactory(commission_percent=Decimal("10.00"))
    lesson = build_lesson(instructor=instructor, status=LessonStatus.CANCELLED)
    build_booking(lesson=lesson, unit_price=Decimal("750.00"))

    records = services.calculate_commission(
        instructor, timezone.localdate() - timedelta(days=7), timezone.localdate(),
        user=finance_user,
    )
    assert records == []


def test_an_instructor_on_zero_percent_earns_nothing(finance_user):
    instructor = InstructorFactory(commission_percent=Decimal("0.00"))
    lesson = build_lesson(instructor=instructor, status=LessonStatus.COMPLETED)
    build_booking(lesson=lesson, unit_price=Decimal("750.00"))

    assert services.calculate_commission(
        instructor, timezone.localdate() - timedelta(days=7), timezone.localdate(),
        user=finance_user,
    ) == []


def test_approve_then_pay_a_commission(finance_user):
    from .factories import CommissionRecordFactory

    record = CommissionRecordFactory()
    services.approve_commission(record, user=finance_user)
    assert record.status == CommissionRecord.Status.APPROVED

    services.pay_commission(record, user=finance_user)
    assert record.status == CommissionRecord.Status.PAID
    assert record.paid_at is not None


def test_an_unapproved_commission_cannot_be_paid(finance_user):
    from .factories import CommissionRecordFactory

    record = CommissionRecordFactory()
    with pytest.raises(ValidationError):
        services.pay_commission(record, user=finance_user)


def test_reception_cannot_approve_commission(reception_user):
    from .factories import CommissionRecordFactory

    record = CommissionRecordFactory()
    with pytest.raises(PermissionDenied):
        services.approve_commission(record, user=reception_user)


def test_commission_owed_counts_pending_and_approved(finance_user):
    from .factories import CommissionRecordFactory

    CommissionRecordFactory(commission_amount=Decimal("100.00"))
    CommissionRecordFactory(
        commission_amount=Decimal("50.00"), status=CommissionRecord.Status.APPROVED
    )
    CommissionRecordFactory(
        commission_amount=Decimal("999.00"),
        status=CommissionRecord.Status.PAID,
        paid_at=timezone.now(),
    )

    assert selectors.commission_owed() == Decimal("150.00")


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
def test_sell_package_issues_the_card_and_takes_the_money(finance_user):
    customer = CustomerFactory()
    package = PricePackageFactory(lesson_count=5, price=Decimal("3000.00"), validity_days=90)

    card, payment = services.sell_package(
        customer, package, PaymentMethod.CARD, finance_user
    )

    assert card.lessons_total == 5
    assert card.lessons_used == 0
    assert card.amount_paid == Decimal("3000.00")
    assert card.expires_on == timezone.localdate() + timedelta(days=90)
    assert payment.amount == Decimal("3000.00")
    assert payment.category == Payment.Category.PACKAGE


def test_a_withdrawn_package_cannot_be_sold(finance_user):
    package = PricePackageFactory(is_active=False)
    with pytest.raises(ValidationError):
        services.sell_package(CustomerFactory(), package, PaymentMethod.CASH, finance_user)


def test_use_package_lesson_settles_the_booking_without_a_second_payment(finance_user):
    customer = CustomerFactory()
    package = PricePackageFactory(lesson_count=5, price=Decimal("3000.00"))
    card, _payment = services.sell_package(customer, package, PaymentMethod.CARD, finance_user)
    booking = build_booking(customer=customer, unit_price=Decimal("600.00"))

    services.use_package_lesson(card, booking, user=finance_user)
    card.refresh_from_db()
    booking.refresh_from_db()

    assert card.lessons_used == 1
    assert booking.paid_amount == Decimal("600.00")
    assert booking.payment_status == PaymentStatus.PAID
    # The sale itself is the only revenue row.
    assert Payment.objects.count() == 1


def test_a_package_cannot_settle_another_customers_booking(finance_user):
    card = CustomerPackageFactory()
    booking = build_booking(customer=CustomerFactory())
    with pytest.raises(ValidationError):
        services.use_package_lesson(card, booking, user=finance_user)


def test_an_exhausted_package_cannot_settle_a_booking(finance_user):
    customer = CustomerFactory()
    card = CustomerPackageFactory(customer=customer, lessons_total=1, lessons_used=1)
    booking = build_booking(customer=customer)
    with pytest.raises(ValidationError):
        services.use_package_lesson(card, booking, user=finance_user)


def test_expire_stale_packages_ages_out_old_cards():
    CustomerPackageFactory(
        purchased_on=timezone.localdate() - timedelta(days=400),
        expires_on=timezone.localdate() - timedelta(days=1),
    )
    assert services.expire_stale_packages() == 1
    assert CustomerPackage.objects.filter(status=CustomerPackage.Status.EXPIRED).count() == 1


def test_package_liability_values_only_undelivered_lessons():
    CustomerPackageFactory(lessons_total=5, lessons_used=2, amount_paid=Decimal("3000.00"))
    assert selectors.package_liability() == Decimal("1800.00")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_financial_summary_compares_with_the_previous_period(finance_user):
    customer = CustomerFactory()
    now = timezone.now()
    services.record_payment(customer, Decimal("1000.00"), paid_at=now, user=finance_user)
    services.record_payment(
        customer, Decimal("500.00"), paid_at=now - timedelta(days=20), user=finance_user
    )

    start = now - timedelta(days=6)
    summary = services.financial_summary(start, now)

    assert summary["revenue"]["current"] == Decimal("1000.00")
    assert summary["revenue"]["previous"] == Decimal("0.00")
    assert summary["revenue"]["change"] == 100.0


def test_financial_summary_nets_expenses_into_the_profit(finance_user):
    customer = CustomerFactory()
    now = timezone.now()
    services.record_payment(customer, Decimal("1000.00"), paid_at=now, user=finance_user)
    ExpenseFactory(
        category=ExpenseCategoryFactory(name="Fuel"),
        amount=Decimal("200.00"),
        tax_amount=Decimal("40.00"),
        spent_on=timezone.localdate(),
    )

    summary = services.financial_summary(now - timedelta(days=6), now)
    assert summary["expenses"]["current"] == Decimal("240.00")
    assert summary["gross_profit"]["current"] == Decimal("760.00")


def test_summary_counts_receivables_from_invoices_and_bare_bookings(finance_user):
    invoice = _issued_invoice(amount=Decimal("400.00"))
    build_booking(unit_price=Decimal("100.00"))

    summary = services.financial_summary(
        timezone.now() - timedelta(days=6), timezone.now()
    )
    assert summary["invoiced_receivables"] == Decimal("400.00")
    assert summary["uninvoiced_receivables"] == Decimal("100.00")
    assert summary["outstanding_receivables"] == Decimal("500.00")
    assert invoice.balance_due == Decimal("400.00")


def test_revenue_by_category_splits_the_takings(finance_user):
    customer = CustomerFactory()
    services.record_payment(
        customer, Decimal("300.00"), category=Payment.Category.LESSON, user=finance_user
    )
    services.record_payment(
        customer, Decimal("100.00"), category=Payment.Category.RENTAL, user=finance_user
    )

    rows = {row["category"]: row["amount"] for row in selectors.revenue_by_category()}
    assert rows[Payment.Category.LESSON] == Decimal("300.00")
    assert rows[Payment.Category.RENTAL] == Decimal("100.00")


def test_profit_and_loss_subtracts_paid_commission(finance_user):
    from .factories import CommissionRecordFactory

    now = timezone.now()
    services.record_payment(CustomerFactory(), Decimal("1000.00"), paid_at=now, user=finance_user)
    record = CommissionRecordFactory(
        commission_amount=Decimal("100.00"), status=CommissionRecord.Status.APPROVED
    )
    services.pay_commission(record, user=finance_user)

    # The window closes after the work, exactly as a day's report would.
    report = services.profit_and_loss(now - timedelta(days=6), timezone.now())
    assert report["revenue_total"] == Decimal("1000.00")
    assert report["commission_paid"] == Decimal("100.00")
    assert report["net_profit"] == Decimal("900.00")


def test_cash_flow_series_buckets_by_day_for_a_short_window(finance_user):
    now = timezone.now()
    services.record_payment(CustomerFactory(), Decimal("100.00"), paid_at=now, user=finance_user)

    series = services.cash_flow_series(now - timedelta(days=6), now)
    assert series["granularity"] == "day"
    assert len(series["points"]) == 7
    assert sum(point["revenue"] for point in series["points"]) == Decimal("100.00")


def test_cash_flow_series_widens_the_buckets_over_a_year(finance_user):
    now = timezone.now()
    series = services.cash_flow_series(now - timedelta(days=700), now)
    assert series["granularity"] == "month"
    assert len(series["points"]) < 30


def test_chart_payload_is_json_safe(finance_user):
    import json

    now = timezone.now()
    services.record_payment(CustomerFactory(), Decimal("100.00"), paid_at=now, user=finance_user)
    payload = services.revenue_chart_payload(now - timedelta(days=6), now)
    assert json.dumps(payload)
    assert payload["revenue"][-1] == 100.0


def test_payments_that_never_arrived_do_not_count_as_revenue():
    PaymentFactory(amount=Decimal("500.00"), status=PaymentStatus.UNPAID)
    assert selectors.net_revenue() == Decimal("0.00")
