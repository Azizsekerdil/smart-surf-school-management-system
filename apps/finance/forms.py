"""Forms for the finance screens.

Money inputs are always ``DecimalField`` bound to ``Decimal`` — a float never
enters the system through a form. Every amount field carries ``step="0.01"`` so
the browser's own spinner cannot produce a third decimal place.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import PaymentMethod
from apps.core.forms_base import TailwindFormMixin
from apps.core.utils import RANGE_CHOICES

from .models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    ExpenseCategory,
    Invoice,
    Payment,
    PricePackage,
    to_money,
)

ZERO = Decimal("0.00")

#: The native ``<input type="date">`` and ``type="datetime-local">`` controls
#: always speak ISO, whatever the interface language is. The Turkish locale's
#: input formats do not include ISO, so the formats are pinned here rather than
#: left to the active locale — otherwise every date typed into the picker would
#: come back as "enter a valid date".
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]


class DateTimeLocalInput(forms.DateTimeInput):
    """``<input type="datetime-local">`` — the native picker, no JS needed."""

    input_type = "datetime-local"


class IsoDateFieldsMixin:
    """Force every date/datetime field on the form to render and read ISO."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.DateTimeField):
                field.input_formats = DATETIME_INPUT_FORMATS
                field.widget.format = "%Y-%m-%dT%H:%M"
                field.widget.input_type = "datetime-local"
            elif isinstance(field, forms.DateField):
                field.input_formats = DATE_INPUT_FORMATS
                field.widget.format = "%Y-%m-%d"
                field.widget.input_type = "date"


MONEY_WIDGET = forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"})
DATE_WIDGET = forms.DateInput()
DATETIME_WIDGET = DateTimeLocalInput(format="%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
class FinanceRangeForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    """The period picker shared by the dashboard and every list screen."""

    range = forms.ChoiceField(
        label=_("Period"), choices=RANGE_CHOICES, required=False, initial="30"
    )
    start = forms.DateField(label=_("From"), required=False, widget=DATE_WIDGET)
    end = forms.DateField(label=_("To"), required=False, widget=DATE_WIDGET)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            raise forms.ValidationError(_("The end date must not be before the start date."))
        return cleaned


class PaymentFilterForm(FinanceRangeForm):
    """Filter bar above the payment ledger."""

    KIND_CHOICES = (
        ("", _("Payments and refunds")),
        ("payments", _("Payments only")),
        ("refunds", _("Refunds only")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Code, customer or reference…")}),
    )
    category = forms.ChoiceField(
        label=_("Category"),
        required=False,
        choices=[("", _("All categories"))] + list(Payment.Category.choices),
    )
    method = forms.ChoiceField(
        label=_("Method"),
        required=False,
        choices=[("", _("All methods"))] + list(PaymentMethod.choices),
    )
    kind = forms.ChoiceField(label=_("Type"), required=False, choices=KIND_CHOICES)


class InvoiceFilterForm(FinanceRangeForm):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Invoice number or customer…")}),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("All statuses"))] + list(Invoice.Status.choices),
    )


class ExpenseFilterForm(FinanceRangeForm):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Description or supplier…")}),
    )
    category = forms.ModelChoiceField(
        label=_("Category"),
        required=False,
        queryset=ExpenseCategory.objects.filter(is_active=True),
        empty_label=_("All categories"),
    )


class CommissionFilterForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("All statuses"))] + list(CommissionRecord.Status.choices),
    )
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Instructor name or code…")}),
    )


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class PaymentForm(IsoDateFieldsMixin, TailwindFormMixin, forms.ModelForm):
    """Take a payment at the counter.

    ``customer`` is deliberately a plain model choice rather than a free-text
    field: money must always attach to a real customer record, otherwise the
    ledger cannot be reconciled.
    """

    class Meta:
        model = Payment
        fields = (
            "customer",
            "amount",
            "method",
            "category",
            "invoice",
            "booking",
            "rental",
            "paid_at",
            "reference",
            "notes",
        )
        widgets = {
            "amount": MONEY_WIDGET,
            "paid_at": DATETIME_WIDGET,
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["paid_at"].initial = timezone.now()
        self.fields["invoice"].required = False
        self.fields["booking"].required = False
        self.fields["rental"].required = False
        self.fields["invoice"].queryset = (
            Invoice.objects.filter(status__in=Invoice.OPEN_STATUSES)
            .select_related("customer")
            .order_by("-issue_date")
        )
        self.fields["invoice"].empty_label = _("No invoice")
        self.fields["booking"].empty_label = _("No booking")
        self.fields["rental"].empty_label = _("No rental")
        for name in ("customer", "invoice", "booking", "rental"):
            self.fields[name].widget.attrs.setdefault("data-searchable", "true")

    def clean_amount(self) -> Decimal:
        amount = to_money(self.cleaned_data.get("amount"))
        if amount <= ZERO:
            raise forms.ValidationError(_("A payment must be greater than zero."))
        return amount

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        invoice = cleaned.get("invoice")
        booking = cleaned.get("booking")
        rental = cleaned.get("rental")

        # A payment attached to somebody else's paperwork is a reconciliation
        # bug waiting to happen, so it is refused at the door.
        for field, obj in (("invoice", invoice), ("booking", booking), ("rental", rental)):
            if obj is not None and customer is not None and obj.customer_id != customer.pk:
                self.add_error(
                    field, _("That record belongs to a different customer.")
                )

        paid_at = cleaned.get("paid_at")
        if paid_at and paid_at > timezone.now() + timedelta(minutes=5):
            self.add_error("paid_at", _("A payment cannot be dated in the future."))
        return cleaned


class RefundForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    """Send money back to a customer against an existing payment."""

    amount = forms.DecimalField(
        label=_("Refund amount"),
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=MONEY_WIDGET,
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Recorded on the audit trail and shown on the customer's history."),
    )

    def __init__(self, *args, payment: Payment | None = None, **kwargs):
        self.payment = payment
        super().__init__(*args, **kwargs)
        if payment is not None:
            # ``max_value`` cannot be changed after the field is built, so the
            # ceiling is enforced in ``clean_amount`` and mirrored in the widget.
            self.fields["amount"].initial = payment.refundable_amount
            self.fields["amount"].widget.attrs["max"] = str(payment.refundable_amount)
            self.fields["amount"].help_text = _("At most %(max)s can still be refunded.") % {
                "max": payment.refundable_amount
            }

    def clean_amount(self) -> Decimal:
        amount = to_money(self.cleaned_data.get("amount"))
        if amount <= ZERO:
            raise forms.ValidationError(_("A refund must be greater than zero."))
        if self.payment is not None and amount > self.payment.refundable_amount:
            raise forms.ValidationError(
                _("Only %(max)s remains refundable on this payment.")
                % {"max": self.payment.refundable_amount}
            )
        return amount

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 4:
            raise forms.ValidationError(_("Describe why the money is going back."))
        return reason


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceForm(IsoDateFieldsMixin, TailwindFormMixin, forms.ModelForm):
    """Header of a manually raised invoice."""

    class Meta:
        model = Invoice
        fields = (
            "customer",
            "booking",
            "rental",
            "issue_date",
            "due_date",
            "discount_amount",
            "tax_rate",
            "notes",
            "terms",
        )
        widgets = {
            "issue_date": DATE_WIDGET,
            "due_date": DATE_WIDGET,
            "discount_amount": MONEY_WIDGET,
            "tax_rate": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "terms": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["booking"].required = False
        self.fields["rental"].required = False
        self.fields["booking"].empty_label = _("No booking")
        self.fields["rental"].empty_label = _("No rental")
        if not self.instance.pk:
            today = timezone.localdate()
            self.fields["issue_date"].initial = today
            self.fields["due_date"].initial = today + timedelta(days=14)

    def clean(self):
        cleaned = super().clean()
        issue_date = cleaned.get("issue_date")
        due_date = cleaned.get("due_date")
        if issue_date and due_date and due_date < issue_date:
            self.add_error("due_date", _("The due date cannot fall before the issue date."))
        if cleaned.get("booking") and cleaned.get("rental"):
            self.add_error("rental", _("An invoice covers a booking or a rental, not both."))
        return cleaned


class InvoiceLineForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    """One line of a manually raised invoice."""

    description = forms.CharField(label=_("Description"), max_length=250, required=False)
    quantity = forms.DecimalField(
        label=_("Quantity"),
        max_digits=8,
        decimal_places=2,
        required=False,
        initial=Decimal("1.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    unit_price = forms.DecimalField(
        label=_("Unit price"),
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=MONEY_WIDGET,
    )
    discount_amount = forms.DecimalField(
        label=_("Discount"),
        max_digits=12,
        decimal_places=2,
        required=False,
        initial=ZERO,
        widget=MONEY_WIDGET,
    )

    @property
    def is_blank(self) -> bool:
        """A row the operator simply left empty is skipped, not rejected."""
        data = self.cleaned_data if self.is_bound and self.is_valid() else {}
        return not (data.get("description") or "").strip() and not to_money(
            data.get("unit_price")
        )

    def clean(self):
        cleaned = super().clean()
        description = (cleaned.get("description") or "").strip()
        unit_price = to_money(cleaned.get("unit_price"))
        quantity = cleaned.get("quantity") or Decimal("1.00")

        if not description and unit_price == ZERO:
            return cleaned  # blank row
        if not description:
            self.add_error("description", _("Describe what is being charged for."))
        if unit_price < ZERO:
            self.add_error("unit_price", _("The unit price cannot be negative."))
        if quantity <= ZERO:
            self.add_error("quantity", _("The quantity must be above zero."))
        if to_money(cleaned.get("discount_amount")) > to_money(quantity * unit_price):
            self.add_error(
                "discount_amount", _("The line discount cannot exceed the line value.")
            )
        return cleaned


InvoiceLineFormSet = forms.formset_factory(InvoiceLineForm, extra=4, max_num=25, validate_max=True)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
class ExpenseForm(IsoDateFieldsMixin, TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = (
            "category",
            "description",
            "amount",
            "tax_amount",
            "spent_on",
            "supplier",
            "invoice_reference",
            "equipment",
            "receipt",
            "is_recurring",
            "recurrence_months",
        )
        widgets = {
            "amount": MONEY_WIDGET,
            "tax_amount": MONEY_WIDGET,
            "spent_on": DATE_WIDGET,
            "recurrence_months": forms.NumberInput(attrs={"min": "1", "max": "60"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True)
        self.fields["equipment"].required = False
        self.fields["equipment"].empty_label = _("Not equipment-specific")
        if not self.instance.pk:
            self.fields["spent_on"].initial = timezone.localdate()

    def clean_amount(self) -> Decimal:
        amount = to_money(self.cleaned_data.get("amount"))
        if amount <= ZERO:
            raise forms.ValidationError(_("An expense must be above zero."))
        return amount

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_recurring") and not cleaned.get("recurrence_months"):
            self.add_error("recurrence_months", _("State how often the cost repeats."))
        spent_on = cleaned.get("spent_on")
        if spent_on and spent_on > timezone.localdate():
            self.add_error("spent_on", _("An expense cannot be dated in the future."))
        return cleaned


class ExpenseCategoryForm(IsoDateFieldsMixin, TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ("code", "name", "is_active", "sort_order")


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
class PricePackageForm(IsoDateFieldsMixin, TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = PricePackage
        fields = (
            "name",
            "code",
            "description",
            "lesson_type",
            "lesson_count",
            "price",
            "validity_days",
            "is_active",
            "sort_order",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "price": MONEY_WIDGET,
            "lesson_count": forms.NumberInput(attrs={"min": "1", "max": "100"}),
            "validity_days": forms.NumberInput(attrs={"min": "1", "max": "1095"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["lesson_type"].required = False
        self.fields["lesson_type"].empty_label = _("Any lesson type")

    def clean_code(self) -> str:
        code = (self.cleaned_data.get("code") or "").strip().upper()
        duplicate = PricePackage.all_objects.filter(code__iexact=code).exclude(
            pk=self.instance.pk
        )
        if duplicate.exists():
            raise forms.ValidationError(_("A package with this code already exists."))
        return code

    def clean_price(self) -> Decimal:
        price = to_money(self.cleaned_data.get("price"))
        if price <= ZERO:
            raise forms.ValidationError(_("A package must have a price."))
        return price


class SellPackageForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    """Sell a package over the counter."""

    customer = forms.ModelChoiceField(label=_("Customer"), queryset=None)
    package = forms.ModelChoiceField(label=_("Package"), queryset=None)
    payment_method = forms.ChoiceField(
        label=_("Payment method"),
        choices=[
            (value, label)
            for value, label in PaymentMethod.choices
            if value != PaymentMethod.PACKAGE
        ],
        initial=PaymentMethod.CARD,
    )
    reference = forms.CharField(
        label=_("Reference"),
        max_length=100,
        required=False,
        help_text=_("Card authorisation or receipt number."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.customers.models import Customer

        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["package"].queryset = PricePackage.objects.filter(
            is_active=True
        ).select_related("lesson_type")


class UsePackageForm(IsoDateFieldsMixin, TailwindFormMixin, forms.Form):
    """Redeem one lesson from a customer's package against a booking."""

    booking = forms.ModelChoiceField(label=_("Booking"), queryset=None)

    def __init__(self, *args, customer_package: CustomerPackage | None = None, **kwargs):
        self.customer_package = customer_package
        super().__init__(*args, **kwargs)
        from apps.bookings.models import Booking

        queryset = Booking.objects.none()
        if customer_package is not None:
            from apps.core.enums import BookingStatus

            queryset = (
                Booking.objects.filter(customer=customer_package.customer)
                .exclude(status__in=[BookingStatus.CANCELLED, BookingStatus.NO_SHOW])
                .order_by("-booked_at")
            )
        self.fields["booking"].queryset = queryset
