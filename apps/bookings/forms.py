"""Forms for the booking desk.

The create form is deliberately *not* a plain ``ModelForm.save()``: creating a
booking has to go through :func:`apps.bookings.services.create_booking` so the
conflict rules, the roster row and the audit entry all happen together. The form
therefore validates shape and leaves the decision to the service.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import BookingSource, BookingStatus, PaymentStatus
from apps.core.forms_base import BaseModelForm, TailwindFormMixin
from apps.core.utils import to_decimal

from .models import Booking, WaitlistEntry

ZERO = Decimal("0.00")


class BookingCreateForm(BaseModelForm):
    """Single-screen booking form driven by HTMX pickers."""

    class Meta:
        model = Booking
        fields = [
            "booking_type",
            "customer",
            "student",
            "lesson",
            "surf_camp",
            "participants",
            "unit_price",
            "discount_amount",
            "source",
            "special_requests",
            "internal_notes",
        ]
        widgets = {
            "special_requests": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 2}),
            # The four pickers are HTMX-driven panels; the inputs below are the
            # value carriers the browser posts back.
            "customer": forms.HiddenInput(),
            "student": forms.HiddenInput(),
            "lesson": forms.HiddenInput(),
            "surf_camp": forms.HiddenInput(),
        }

    confirm_immediately = forms.BooleanField(
        label=_("Confirm straight away"),
        required=False,
        initial=True,
        help_text=_("Leave unticked to hold the seat as a pending request."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participants"].widget.attrs.update({"min": 1, "max": 30})
        self.fields["source"].initial = BookingSource.WALK_IN
        self.fields["unit_price"].required = False
        self.fields["discount_amount"].required = False
        self.fields["student"].required = False
        self.fields["lesson"].required = False
        self.fields["surf_camp"].required = False
        # The four pickers are HTMX panels; these hidden inputs carry the value
        # and are bound to the Alpine state that the panels write into.
        for name, model_name in (
            ("customer", "customerId"),
            ("student", "studentId"),
            ("lesson", "lessonId"),
            ("surf_camp", "campId"),
        ):
            self.fields[name].widget.attrs.update({"x-model": model_name})

    def clean_participants(self):
        value = self.cleaned_data.get("participants") or 1
        if value < 1:
            raise forms.ValidationError(_("A booking must have at least one participant."))
        if value > 30:
            raise forms.ValidationError(
                _("A single booking cannot hold more than 30 seats. Split it into two.")
            )
        return value

    def clean_unit_price(self):
        return to_decimal(self.cleaned_data.get("unit_price"))

    def clean_discount_amount(self):
        value = to_decimal(self.cleaned_data.get("discount_amount"))
        if value < ZERO:
            raise forms.ValidationError(_("A discount cannot be negative."))
        return value

    def clean(self):
        cleaned = super().clean()
        booking_type = cleaned.get("booking_type")
        if booking_type == Booking.BookingType.LESSON:
            if not cleaned.get("lesson"):
                self.add_error("lesson", _("Choose the lesson this booking is for."))
            if not cleaned.get("student"):
                self.add_error("student", _("Choose the student who will attend."))
            cleaned["surf_camp"] = None
        elif booking_type == Booking.BookingType.CAMP:
            if not cleaned.get("surf_camp"):
                self.add_error("surf_camp", _("Choose the surf camp this booking is for."))
            cleaned["lesson"] = None

        discount = to_decimal(cleaned.get("discount_amount"))
        price = to_decimal(cleaned.get("unit_price"))
        seats = cleaned.get("participants") or 1
        if price and discount > price * seats:
            self.add_error(
                "discount_amount", _("The discount cannot exceed the price of the booking.")
            )
        return cleaned


class BookingUpdateForm(BaseModelForm):
    """Edit the commercial and operational details of an existing booking.

    Status is not editable here — status changes go through the guarded action
    buttons so every transition is audited and re-validated.
    """

    class Meta:
        model = Booking
        fields = [
            "participants",
            "unit_price",
            "discount_amount",
            "source",
            "special_requests",
            "internal_notes",
        ]
        widgets = {
            "special_requests": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_participants(self):
        value = self.cleaned_data.get("participants") or 1
        if value < 1:
            raise forms.ValidationError(_("A booking must have at least one participant."))
        return value


class BookingCancelForm(TailwindFormMixin, forms.Form):
    """Reason plus an optional fee override."""

    REASON_CHOICES = (
        ("customer_request", _("Customer requested it")),
        ("weather", _("Weather / unsafe conditions")),
        ("illness", _("Illness or injury")),
        ("school_cancelled", _("School cancelled the session")),
        ("duplicate", _("Duplicate booking")),
        ("other", _("Other")),
    )

    reason_code = forms.ChoiceField(label=_("Reason"), choices=REASON_CHOICES)
    reason = forms.CharField(
        label=_("Details"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Written to the audit trail and shown on the booking."),
    )
    override_fee = forms.BooleanField(
        label=_("Override the automatic cancellation fee"), required=False
    )
    fee = forms.DecimalField(
        label=_("Cancellation fee"),
        required=False,
        min_value=ZERO,
        max_digits=12,
        decimal_places=2,
    )

    def __init__(self, *args, suggested_fee: Decimal | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.suggested_fee = to_decimal(suggested_fee)
        self.fields["fee"].initial = self.suggested_fee

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("override_fee") and cleaned.get("fee") is None:
            self.add_error("fee", _("Enter the fee you want to charge."))
        detail = (cleaned.get("reason") or "").strip()
        if not detail:
            self.add_error("reason", _("Record why the booking was cancelled."))
        return cleaned

    @property
    def resolved_fee(self) -> Decimal | None:
        """The fee to apply, or ``None`` to let the policy decide."""
        if not self.cleaned_data.get("override_fee"):
            return None
        return to_decimal(self.cleaned_data.get("fee"))

    @property
    def full_reason(self) -> str:
        label = dict(self.REASON_CHOICES).get(self.cleaned_data.get("reason_code"), "")
        detail = (self.cleaned_data.get("reason") or "").strip()
        return f"{label} — {detail}" if label else detail


class WaitlistEntryForm(BaseModelForm):
    class Meta:
        model = WaitlistEntry
        fields = ["lesson", "surf_camp", "customer", "student", "participants", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lesson"].required = False
        self.fields["surf_camp"].required = False
        self.fields["student"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("lesson") and not cleaned.get("surf_camp"):
            self.add_error("lesson", _("Choose the lesson or the surf camp being waited for."))
        if cleaned.get("lesson") and cleaned.get("surf_camp"):
            self.add_error(
                "surf_camp", _("An entry waits for a lesson or a surf camp, not both.")
            )
        return cleaned


class BookingPaymentForm(TailwindFormMixin, forms.Form):
    """Record money taken at the desk against a booking."""

    amount = forms.DecimalField(
        label=_("Amount received"), min_value=Decimal("0.01"), max_digits=12, decimal_places=2
    )

    def __init__(self, *args, balance: Decimal | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.balance = to_decimal(balance)
        if self.balance > ZERO:
            self.fields["amount"].initial = self.balance
            self.fields["amount"].help_text = _("Outstanding balance: %(balance)s") % {
                "balance": self.balance
            }


class BookingFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the booking list."""

    q = forms.CharField(label=_("Search"), required=False)
    status = forms.ChoiceField(label=_("Status"), required=False, choices=())
    payment_status = forms.ChoiceField(label=_("Payment"), required=False, choices=())
    booking_type = forms.ChoiceField(label=_("Type"), required=False, choices=())
    start = forms.DateField(label=_("Booked from"), required=False, widget=forms.DateInput())
    end = forms.DateField(label=_("Booked to"), required=False, widget=forms.DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            ("", _("All statuses")),
            ("active", _("Active (pending, confirmed, checked in)")),
            *BookingStatus.choices,
        ]
        self.fields["payment_status"].choices = [
            ("", _("All payments")),
            ("outstanding", _("Outstanding balance")),
            *PaymentStatus.choices,
        ]
        self.fields["booking_type"].choices = [
            ("", _("All types")),
            *Booking.BookingType.choices,
        ]
