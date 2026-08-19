"""Forms for the hire counter.

The check-out and check-in screens are the two places where a mistake costs the
school real money, so both forms validate hard and hand the decision to
:mod:`apps.rentals.services`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    DamageType,
    EquipmentCondition,
    PaymentMethod,
    RentalPeriod,
)
from apps.core.forms_base import BaseModelForm, TailwindFormMixin

from .models import Rental, RentalItem

ZERO = Decimal("0.00")

#: Default hire length per period, used to pre-fill the due-back time.
DEFAULT_WINDOW = {
    RentalPeriod.HOURLY: timedelta(hours=2),
    RentalPeriod.DAILY: timedelta(days=1),
    RentalPeriod.WEEKLY: timedelta(days=7),
}


class DateTimeLocalInput(forms.DateTimeInput):
    """A native browser date+time picker."""

    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%dT%H:%M")


DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"]


class RentalCheckOutForm(BaseModelForm):
    """The counter form. Equipment lines come from the check-out basket."""

    customer_label = forms.CharField(required=False, widget=forms.HiddenInput())
    student_label = forms.CharField(required=False, widget=forms.HiddenInput())
    booking_label = forms.CharField(required=False, widget=forms.HiddenInput())
    paid_amount = forms.DecimalField(
        label=_("Paid now"),
        max_digits=12,
        decimal_places=2,
        min_value=ZERO,
        required=False,
        initial=ZERO,
        help_text=_("Leave at zero to invoice the hire and take payment later."),
    )
    payment_method = forms.ChoiceField(
        label=_("Payment method"),
        choices=[("", "—")] + list(PaymentMethod.choices),
        required=False,
    )

    class Meta:
        model = Rental
        fields = [
            "customer",
            "student",
            "booking",
            "period_type",
            "start_at",
            "expected_return_at",
            "deposit_amount",
            "discount_amount",
            "id_document_held",
            "notes",
        ]
        widgets = {
            "customer": forms.HiddenInput(),
            "student": forms.HiddenInput(),
            "booking": forms.HiddenInput(),
            "start_at": DateTimeLocalInput(),
            "expected_return_at": DateTimeLocalInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("start_at", "expected_return_at"):
            self.fields[name].input_formats = DATETIME_INPUT_FORMATS
        self.fields["notes"].required = False

        if not self.is_bound:
            now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
            period = self.initial.get("period_type") or RentalPeriod.DAILY
            self.initial.setdefault("start_at", now)
            self.initial.setdefault(
                "expected_return_at", now + DEFAULT_WINDOW.get(period, timedelta(days=1))
            )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_at")
        end = cleaned.get("expected_return_at")
        if start and end and end <= start:
            self.add_error(
                "expected_return_at", _("The due-back time must be after the start time.")
            )
        deposit = cleaned.get("deposit_amount") or ZERO
        if deposit < ZERO:
            self.add_error("deposit_amount", _("A deposit cannot be negative."))
        return cleaned


class RentalUpdateForm(BaseModelForm):
    """Administrative edits that do not move equipment or re-price the hire."""

    class Meta:
        model = Rental
        fields = [
            "student",
            "booking",
            "deposit_amount",
            "discount_amount",
            "id_document_held",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_discount_amount(self):
        discount = self.cleaned_data.get("discount_amount") or ZERO
        if self.instance.pk and discount > (self.instance.subtotal or ZERO):
            raise forms.ValidationError(
                _("The discount cannot exceed the hire charge of %(amount)s.")
                % {"amount": self.instance.subtotal}
            )
        return discount


class RentalExtendForm(TailwindFormMixin, forms.Form):
    """Push the due-back time out; the hire is re-priced by the service."""

    new_return_at = forms.DateTimeField(
        label=_("New due-back time"),
        widget=DateTimeLocalInput(),
        input_formats=DATETIME_INPUT_FORMATS,
    )

    def __init__(self, *args, rental: Rental | None = None, **kwargs):
        self.rental = rental
        super().__init__(*args, **kwargs)
        if rental is not None and not self.is_bound:
            self.initial.setdefault(
                "new_return_at",
                timezone.localtime(rental.expected_return_at) + timedelta(days=1),
            )

    def clean_new_return_at(self):
        value = self.cleaned_data["new_return_at"]
        if self.rental is not None and value <= self.rental.expected_return_at:
            raise forms.ValidationError(
                _("Choose a time later than the current due-back time.")
            )
        return value


class RentalReturnItemForm(TailwindFormMixin, forms.Form):
    """Check-in data for one asset. Instantiated with ``prefix='item-<pk>'``."""

    check_in = forms.BooleanField(label=_("Coming back now"), required=False, initial=True)
    condition_in = forms.ChoiceField(
        label=_("Condition"), choices=EquipmentCondition.choices
    )
    damage_reported = forms.BooleanField(label=_("Damage"), required=False)
    damage_type = forms.ChoiceField(
        label=_("Damage type"), choices=[("", "—")] + list(DamageType.choices), required=False
    )
    damage_notes = forms.CharField(
        label=_("What happened"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    damage_charge = forms.DecimalField(
        label=_("Damage charge"),
        max_digits=12,
        decimal_places=2,
        min_value=ZERO,
        required=False,
        initial=ZERO,
    )

    def __init__(self, *args, item: RentalItem | None = None, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        if item is not None and not self.is_bound:
            self.initial.setdefault("condition_in", item.condition_out)

    def clean(self):
        cleaned = super().clean()
        damaged = cleaned.get("damage_reported")
        condition = cleaned.get("condition_in")
        charge = cleaned.get("damage_charge") or ZERO

        if condition == EquipmentCondition.UNUSABLE and not damaged:
            cleaned["damage_reported"] = damaged = True
        if damaged and not cleaned.get("damage_type"):
            self.add_error("damage_type", _("Select the kind of damage."))
        if charge > ZERO and not damaged:
            self.add_error(
                "damage_charge", _("Tick “Damage” before charging for a repair.")
            )
        if not damaged:
            cleaned["damage_charge"] = ZERO
        return cleaned

    @property
    def charge(self) -> Decimal:
        if not self.is_valid():
            return ZERO
        return self.cleaned_data.get("damage_charge") or ZERO


def build_return_forms(rental: Rental, data=None) -> list[RentalReturnItemForm]:
    """One :class:`RentalReturnItemForm` per item still out."""
    forms_list = []
    for item in rental.items.select_related("equipment").filter(returned_at__isnull=True):
        forms_list.append(
            RentalReturnItemForm(data or None, prefix=f"item-{item.pk}", item=item)
        )
    return forms_list


def item_conditions_from_forms(item_forms) -> dict[int, tuple]:
    """Translate validated check-in forms into the services payload."""
    payload: dict[int, tuple] = {}
    for form in item_forms:
        if not form.is_valid() or form.item is None:
            continue
        if not form.cleaned_data.get("check_in"):
            continue
        payload[form.item.pk] = (
            form.cleaned_data.get("condition_in") or form.item.condition_out,
            form.cleaned_data.get("damage_type") or "",
            form.cleaned_data.get("damage_notes") or "",
            form.cleaned_data.get("damage_charge") or ZERO,
        )
    return payload


class QuickReturnForm(TailwindFormMixin, forms.Form):
    """Scan-and-drop returns straight from the rental list."""

    asset_code = forms.CharField(
        label=_("Asset code"),
        max_length=64,
        widget=forms.TextInput(
            attrs={"placeholder": _("Scan or type an asset code"), "autocomplete": "off"}
        ),
    )


class AddItemForm(TailwindFormMixin, forms.Form):
    """Adds a line to the check-out basket by asset code."""

    asset_code = forms.CharField(label=_("Asset code"), max_length=64)
    quantity = forms.IntegerField(label=_("Quantity"), min_value=1, initial=1, required=False)


class RentalPaymentForm(TailwindFormMixin, forms.Form):
    amount = forms.DecimalField(
        label=_("Amount"), max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    method = forms.ChoiceField(
        label=_("Method"), choices=PaymentMethod.choices, initial=PaymentMethod.CASH
    )


class RentalCancelForm(TailwindFormMixin, forms.Form):
    reason = forms.CharField(
        label=_("Reason"), max_length=250, widget=forms.Textarea(attrs={"rows": 2})
    )


class RentalLostForm(TailwindFormMixin, forms.Form):
    """Write off equipment that is never coming back."""

    replacement_charge = forms.DecimalField(
        label=_("Replacement charge"),
        max_digits=12,
        decimal_places=2,
        min_value=ZERO,
        initial=ZERO,
        help_text=_("Charged to the customer and split across the missing items."),
    )
    reason = forms.CharField(
        label=_("Notes"), max_length=250, widget=forms.Textarea(attrs={"rows": 2})
    )
