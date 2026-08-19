"""Model-level tests: the arithmetic and the invariants."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.finance.models import (
    CustomerPackage,
    Invoice,
    InvoiceLine,
    Payment,
    next_invoice_number,
    to_money,
)

from .factories import (
    CommissionRecordFactory,
    CustomerPackageFactory,
    ExpenseFactory,
    InvoiceFactory,
    InvoiceLineFactory,
    PaymentFactory,
    PricePackageFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Invoice numbering
# ---------------------------------------------------------------------------
def test_invoice_number_is_year_scoped_and_sequential():
    today = timezone.localdate()
    first = InvoiceFactory(issue_date=today)
    second = InvoiceFactory(issue_date=today)

    assert first.invoice_number == f"INV-{today.year}-00001"
    assert second.invoice_number == f"INV-{today.year}-00002"


def test_invoice_number_restarts_each_year():
    InvoiceFactory(issue_date=timezone.localdate().replace(month=6, day=1))
    other_year = timezone.localdate().replace(month=6, day=1) - timedelta(days=400)
    invoice = InvoiceFactory(issue_date=other_year)
    assert invoice.invoice_number == f"INV-{other_year.year}-00001"


def test_next_invoice_number_ignores_other_years():
    year = timezone.localdate().year
    InvoiceFactory(issue_date=timezone.localdate())
    assert next_invoice_number(year + 5) == f"INV-{year + 5}-00001"


def test_invoice_str_includes_the_number():
    invoice = InvoiceFactory()
    assert invoice.invoice_number in str(invoice)


# ---------------------------------------------------------------------------
# Invoice arithmetic
# ---------------------------------------------------------------------------
def test_recalculate_applies_the_discount_before_the_tax():
    invoice = InvoiceFactory(discount_amount=Decimal("100.00"), tax_rate=Decimal("20.00"))
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("500.00"), quantity=Decimal("2.00"))
    invoice.recalculate()

    assert invoice.subtotal == Decimal("1000.00")
    # (1000 - 100) * 20% = 180
    assert invoice.tax_amount == Decimal("180.00")
    assert invoice.total_amount == Decimal("1080.00")


def test_recalculate_caps_the_discount_at_the_subtotal():
    invoice = InvoiceFactory(discount_amount=Decimal("999.00"))
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"), quantity=Decimal("1.00"))
    invoice.recalculate()

    assert invoice.discount_amount == Decimal("100.00")
    assert invoice.total_amount == Decimal("0.00")


def test_tax_rounds_half_up_to_the_cent():
    invoice = InvoiceFactory(tax_rate=Decimal("18.00"))
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("33.25"), quantity=Decimal("1.00"))
    invoice.recalculate()

    # 33.25 * 0.18 = 5.985 -> 5.99, never 5.98
    assert invoice.tax_amount == Decimal("5.99")
    assert invoice.total_amount == Decimal("39.24")


def test_balance_due_and_is_paid():
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("200.00"))
    invoice.recalculate()

    assert invoice.balance_due == Decimal("200.00")
    assert invoice.is_paid is False

    invoice.paid_amount = Decimal("200.00")
    assert invoice.balance_due == Decimal("0.00")
    assert invoice.is_paid is True


def test_an_issued_invoice_past_its_due_date_is_overdue():
    invoice = InvoiceFactory(
        status=Invoice.Status.ISSUED,
        due_date=timezone.localdate() - timedelta(days=3),
        total_amount=Decimal("100.00"),
    )
    assert invoice.is_overdue is True
    assert invoice.days_overdue == 3


def test_a_draft_is_never_overdue():
    invoice = InvoiceFactory(
        status=Invoice.Status.DRAFT,
        due_date=timezone.localdate() - timedelta(days=10),
        total_amount=Decimal("100.00"),
    )
    assert invoice.is_overdue is False


def test_refresh_status_marks_a_partly_paid_invoice():
    invoice = InvoiceFactory(status=Invoice.Status.ISSUED)
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("500.00"))
    invoice.paid_amount = Decimal("200.00")
    invoice.recalculate()
    assert invoice.status == Invoice.Status.PARTIAL

    invoice.paid_amount = Decimal("500.00")
    invoice.recalculate()
    assert invoice.status == Invoice.Status.PAID


def test_a_cancelled_invoice_keeps_its_status():
    invoice = InvoiceFactory(status=Invoice.Status.CANCELLED)
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("500.00"))
    invoice.recalculate()
    assert invoice.status == Invoice.Status.CANCELLED


def test_invoice_rejects_a_due_date_before_the_issue_date():
    invoice = InvoiceFactory.build(
        issue_date=timezone.localdate(),
        due_date=timezone.localdate() - timedelta(days=1),
    )
    with pytest.raises(ValidationError) as error:
        invoice.clean()
    assert "due_date" in error.value.message_dict


# ---------------------------------------------------------------------------
# Invoice lines
# ---------------------------------------------------------------------------
def test_line_total_is_quantity_times_price_less_the_discount():
    line = InvoiceLineFactory(
        quantity=Decimal("3.00"), unit_price=Decimal("120.50"), discount_amount=Decimal("20.00")
    )
    assert line.line_total == Decimal("341.50")


def test_line_total_never_goes_below_zero():
    line = InvoiceLine(
        invoice=InvoiceFactory(),
        description="Odd discount",
        quantity=Decimal("1.00"),
        unit_price=Decimal("10.00"),
        discount_amount=Decimal("50.00"),
    )
    assert line.compute_total() == Decimal("0.00")


def test_line_rejects_a_discount_larger_than_the_line():
    line = InvoiceLineFactory.build(
        quantity=Decimal("1.00"), unit_price=Decimal("10.00"), discount_amount=Decimal("50.00")
    )
    with pytest.raises(ValidationError) as error:
        line.clean()
    assert "discount_amount" in error.value.message_dict


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def test_payment_code_is_sequential():
    first = PaymentFactory()
    second = PaymentFactory()
    assert first.payment_code == "PAY00001"
    assert second.payment_code == "PAY00002"


def test_payment_of_zero_is_rejected():
    payment = PaymentFactory.build(amount=Decimal("0.00"))
    with pytest.raises(ValidationError) as error:
        payment.clean()
    assert "amount" in error.value.message_dict


def test_a_negative_amount_must_be_flagged_as_a_refund():
    payment = PaymentFactory.build(amount=Decimal("-50.00"), is_refund=False)
    with pytest.raises(ValidationError) as error:
        payment.clean()
    assert "amount" in error.value.message_dict


def test_a_refund_must_carry_a_reason():
    payment = PaymentFactory.build(
        amount=Decimal("-50.00"), is_refund=True, refund_reason=""
    )
    with pytest.raises(ValidationError) as error:
        payment.clean()
    assert "refund_reason" in error.value.message_dict


def test_refundable_amount_falls_as_refunds_are_written():
    original = PaymentFactory(amount=Decimal("300.00"))
    Payment.objects.create(
        customer=original.customer,
        amount=Decimal("-100.00"),
        is_refund=True,
        refunded_payment=original,
        refund_reason="Half day cancelled by weather.",
    )
    original.refresh_from_db()

    assert original.refunded_amount == Decimal("100.00")
    assert original.refundable_amount == Decimal("200.00")
    assert original.can_refund is True
    assert original.is_fully_refunded is False


def test_a_refund_row_cannot_itself_be_refunded():
    original = PaymentFactory(amount=Decimal("100.00"))
    refund = Payment.objects.create(
        customer=original.customer,
        amount=Decimal("-100.00"),
        is_refund=True,
        refunded_payment=original,
        refund_reason="Cancelled.",
    )
    assert refund.can_refund is False
    assert refund.refundable_amount == Decimal("0.00")

    original.refresh_from_db()
    assert original.is_fully_refunded is True


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
def test_expense_code_is_allocated_and_total_includes_tax():
    expense = ExpenseFactory(amount=Decimal("100.00"), tax_amount=Decimal("18.00"))
    assert expense.expense_code == "EXP00001"
    assert expense.total_amount == Decimal("118.00")


def test_recurring_expense_reports_its_next_due_date():
    expense = ExpenseFactory(
        spent_on=timezone.localdate().replace(month=1, day=31),
        is_recurring=True,
        recurrence_months=1,
    )
    # 31 January + 1 month lands on the last day February actually has.
    assert expense.next_due_on.month == 2
    assert expense.next_due_on.day in (28, 29)


def test_a_recurring_expense_needs_an_interval():
    expense = ExpenseFactory.build(is_recurring=True, recurrence_months=None)
    with pytest.raises(ValidationError) as error:
        expense.clean()
    assert "recurrence_months" in error.value.message_dict


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
def test_commission_amount_is_base_times_percent():
    record = CommissionRecordFactory.build(
        base_amount=Decimal("1234.56"), commission_percent=Decimal("12.50")
    )
    assert record.compute_amount() == Decimal("154.32")


def test_commission_rejects_an_inverted_period():
    record = CommissionRecordFactory.build(
        period_start=timezone.localdate(),
        period_end=timezone.localdate() - timedelta(days=1),
    )
    with pytest.raises(ValidationError) as error:
        record.clean()
    assert "period_end" in error.value.message_dict


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
def test_price_per_lesson_and_saving_against_single_lessons():
    from apps.lessons.tests.factories import LessonTypeFactory

    lesson_type = LessonTypeFactory(base_price=Decimal("750.00"))
    package = PricePackageFactory(
        lesson_type=lesson_type, lesson_count=5, price=Decimal("3000.00")
    )

    assert package.price_per_lesson == Decimal("600.00")
    assert package.saving_vs_single == Decimal("750.00")
    assert package.saving_percent == Decimal("20.00")


def test_saving_is_zero_when_no_lesson_type_is_named():
    package = PricePackageFactory(lesson_type=None)
    assert package.saving_vs_single == Decimal("0.00")


def test_consume_reduces_the_remaining_lessons():
    card = CustomerPackageFactory(lessons_total=5, lessons_used=0)
    assert card.consume() == 4
    card.refresh_from_db()
    assert card.lessons_used == 1
    assert card.status == CustomerPackage.Status.ACTIVE


def test_consuming_the_last_lesson_exhausts_the_card():
    card = CustomerPackageFactory(lessons_total=2, lessons_used=1)
    card.consume()
    card.refresh_from_db()
    assert card.lessons_remaining == 0
    assert card.status == CustomerPackage.Status.EXHAUSTED
    assert card.is_usable is False


def test_consuming_an_exhausted_card_is_refused():
    card = CustomerPackageFactory(lessons_total=1, lessons_used=1)
    with pytest.raises(ValidationError):
        card.consume()


def test_consuming_an_expired_card_is_refused_and_expires_it():
    card = CustomerPackageFactory(
        purchased_on=timezone.localdate() - timedelta(days=200),
        expires_on=timezone.localdate() - timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        card.consume()
    card.refresh_from_db()
    assert card.status == CustomerPackage.Status.EXPIRED


def test_usage_percent_and_value_per_lesson():
    card = CustomerPackageFactory(
        lessons_total=4, lessons_used=1, amount_paid=Decimal("2000.00")
    )
    assert card.usage_percent == 25
    assert card.value_per_lesson == Decimal("500.00")


def test_card_rejects_more_used_than_it_holds():
    card = CustomerPackageFactory.build(lessons_total=2, lessons_used=3)
    with pytest.raises(ValidationError) as error:
        card.clean()
    assert "lessons_used" in error.value.message_dict


# ---------------------------------------------------------------------------
# to_money
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.005", Decimal("1.01")),
        ("2.344", Decimal("2.34")),
        (None, Decimal("0.00")),
        ("", Decimal("0.00")),
        ("not a number", Decimal("0.00")),
    ],
)
def test_to_money_rounds_half_up_and_never_raises(raw, expected):
    assert to_money(raw) == expected
