"""Forms for reporting, working and closing maintenance."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import STAFF_ROLES
from apps.core.enums import EquipmentCondition, EquipmentStatus
from apps.core.forms_base import BaseModelForm, TailwindFormMixin

from . import selectors
from .models import MaintenanceRecord, MaintenanceSchedule


def _staff_queryset():
    """Users who can realistically be handed a repair job."""
    from django.contrib.auth import get_user_model

    return (
        get_user_model()
        .objects.filter(is_active=True, role__in=list(STAFF_ROLES))
        .order_by("first_name", "last_name")
    )


def _serviceable_equipment_queryset():
    """Equipment that can still receive maintenance work."""
    model = selectors.get_equipment_model()
    if model is None:  # pragma: no cover - equipment module always installed
        return None
    queryset = model.objects.all()
    if selectors.model_has_field(model, "status"):
        queryset = queryset.exclude(
            status__in=(EquipmentStatus.RETIRED, EquipmentStatus.LOST)
        )
    if selectors.model_has_field(model, "category"):
        queryset = queryset.select_related("category")
    return queryset


class CheckListField(forms.CharField):
    """A textarea that stores one check per line as a JSON list of strings."""

    widget = forms.Textarea

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            return "\n".join(str(item) for item in value)
        return value

    def clean(self, value):
        text = super().clean(value) or ""
        return [line.strip() for line in text.splitlines() if line.strip()]


class MaintenanceReportForm(BaseModelForm):
    """Report a problem. Reachable from an equipment page with the item filled in."""

    force = forms.BooleanField(
        label=_("Report as a separate issue"),
        required=False,
        help_text=_(
            "Tick only if this is genuinely new damage while another record of the "
            "same type is still open."
        ),
    )

    class Meta:
        model = MaintenanceRecord
        fields = [
            "equipment",
            "damage_type",
            "severity",
            "description",
            "photo_before",
            "made_unusable",
            "assigned_to",
            "rental_item",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "made_unusable": _("Take the item out of service now"),
        }
        help_texts = {
            "made_unusable": _(
                "Leave ticked unless the damage is purely cosmetic and the item is "
                "still safe to hand to a customer."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        equipment_queryset = _serviceable_equipment_queryset()
        if equipment_queryset is not None:
            self.fields["equipment"].queryset = equipment_queryset
        self.fields["assigned_to"].queryset = _staff_queryset()
        self.fields["assigned_to"].required = False
        self.fields["rental_item"].required = False
        self.fields["description"].widget.attrs.setdefault(
            "placeholder", _("Where on the item, how big, and how it happened.")
        )

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        rental_item = cleaned.get("rental_item")
        if equipment is not None and rental_item is not None:
            rental_equipment_id = getattr(rental_item, "equipment_id", None)
            if rental_equipment_id is not None and rental_equipment_id != equipment.pk:
                self.add_error(
                    "rental_item", _("That rental line is for a different piece of equipment.")
                )
        return cleaned


class MaintenanceRecordForm(BaseModelForm):
    """Edit the description of an existing record.

    Status, costs and the equipment link are deliberately absent: they change
    through the workflow actions so the audit trail stays truthful.
    """

    class Meta:
        model = MaintenanceRecord
        fields = [
            "damage_type",
            "severity",
            "description",
            "diagnosis",
            "assigned_to",
            "made_unusable",
            "photo_before",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "diagnosis": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "made_unusable": _("Item is out of service"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _staff_queryset()
        self.fields["assigned_to"].required = False


class MaintenanceCompletionForm(TailwindFormMixin, forms.Form):
    """Close a repair: what was done, what it cost, and what happens to the item."""

    resolution = forms.CharField(
        label=_("What was done"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    parts_used = forms.CharField(
        label=_("Parts and materials used"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("One per line, e.g. 'epoxy resin 250 ml'."),
    )
    labour_hours = forms.DecimalField(
        label=_("Labour hours"),
        min_value=Decimal("0.00"),
        max_digits=5,
        decimal_places=2,
        initial=Decimal("0.00"),
    )
    parts_cost = forms.DecimalField(
        label=_("Parts cost"),
        min_value=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        initial=Decimal("0.00"),
    )
    labour_cost = forms.DecimalField(
        label=_("Labour cost"),
        min_value=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text=_("Leave empty to calculate it from the hours and the workshop rate."),
    )
    photo_after = forms.ImageField(label=_("Photo — after"), required=False)
    condition_after = forms.ChoiceField(
        label=_("Condition after the repair"),
        choices=[("", _("Leave unchanged"))] + list(EquipmentCondition.choices),
        required=False,
    )
    still_unusable = forms.BooleanField(
        label=_("Item is still not safe to use"),
        required=False,
        help_text=_("Keeps the item out of the rental pool after this record closes."),
    )
    retire_equipment = forms.BooleanField(
        label=_("Write the item off"),
        required=False,
        help_text=_("Retires the item permanently — it will never be offered again."),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("retire_equipment") and cleaned.get("still_unusable"):
            # Retiring already implies it never goes back out; keep one meaning.
            cleaned["still_unusable"] = False
        return cleaned

    def as_costs(self) -> dict:
        return {
            "labour_hours": self.cleaned_data.get("labour_hours") or Decimal("0.00"),
            "parts_cost": self.cleaned_data.get("parts_cost") or Decimal("0.00"),
            "labour_cost": self.cleaned_data.get("labour_cost"),
            "parts_used": self.cleaned_data.get("parts_used") or "",
        }


class MaintenanceReasonForm(TailwindFormMixin, forms.Form):
    """Shared form for the actions that require a written reason."""

    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
    )


class MaintenanceStartForm(TailwindFormMixin, forms.Form):
    diagnosis = forms.CharField(
        label=_("Initial diagnosis"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    assigned_to = forms.ModelChoiceField(
        label=_("Assign to"), queryset=None, required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _staff_queryset()


class MaintenanceScheduleForm(BaseModelForm):
    """Define the preventive plan for one item."""

    check_items = CheckListField(
        label=_("Check list"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("One check per line, e.g. 'Inspect leash plug for movement'."),
    )

    class Meta:
        model = MaintenanceSchedule
        fields = [
            "equipment",
            "interval_days",
            "last_performed_on",
            "check_items",
            "is_active",
        ]
        widgets = {
            "last_performed_on": forms.DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        equipment_queryset = _serviceable_equipment_queryset()
        if equipment_queryset is not None:
            if self.instance.pk:
                self.fields["equipment"].disabled = True
                self.fields["equipment"].queryset = equipment_queryset.filter(
                    pk=self.instance.equipment_id
                )
            else:
                # One schedule per item; hide the ones that already have a plan.
                taken = MaintenanceSchedule.objects.values_list("equipment_id", flat=True)
                self.fields["equipment"].queryset = equipment_queryset.exclude(pk__in=taken)

    def clean_last_performed_on(self):
        value = self.cleaned_data.get("last_performed_on")
        if value and value > timezone.localdate():
            raise forms.ValidationError(_("The last service cannot be in the future."))
        return value


class SchedulePerformedForm(TailwindFormMixin, forms.Form):
    performed_on = forms.DateField(
        label=_("Performed on"), widget=forms.DateInput(), required=False
    )

    def clean_performed_on(self):
        value = self.cleaned_data.get("performed_on") or timezone.localdate()
        if value > timezone.localdate():
            raise forms.ValidationError(_("A service cannot be recorded in the future."))
        return value
