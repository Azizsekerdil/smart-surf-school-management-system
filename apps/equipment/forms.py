"""Forms for the equipment screens.

Status is deliberately **not** editable through the item form: every status move
goes through :func:`apps.equipment.services.change_status`, which enforces the
state machine and writes the audit entry. A form field would let an operator
walk around both.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import EquipmentCondition, EquipmentStatus, SurfLevel
from apps.core.forms_base import BaseModelForm, TailwindFormMixin
from apps.core.validators import validate_document_upload

from .models import Equipment, EquipmentCategory, EquipmentPhoto
from .selectors import category_tree
from .services import STATUS_REASON_REQUIRED, allowed_next_statuses


class EquipmentForm(BaseModelForm):
    """Create / edit one item of equipment."""

    category = forms.ModelChoiceField(
        label=_("Category"),
        queryset=EquipmentCategory.objects.none(),
        empty_label=None,
    )

    class Meta:
        model = Equipment
        fields = (
            "asset_code",
            "category",
            "name",
            "brand",
            "model",
            "serial_number",
            "size_label",
            "length_cm",
            "width_cm",
            "thickness_cm",
            "volume_litres",
            "wetsuit_thickness",
            "suitable_min_level",
            "suitable_max_level",
            "min_rider_weight_kg",
            "max_rider_weight_kg",
            "condition",
            "storage_location",
            "purchase_date",
            "purchase_price",
            "current_value",
            "supplier",
            "is_rentable",
            "is_lesson_stock",
            "rental_price_hourly",
            "rental_price_daily",
            "rental_price_weekly",
            "deposit_amount",
            "last_maintenance_date",
            "next_maintenance_date",
            "notes",
        )
        widgets = {
            "purchase_date": forms.DateInput(),
            "last_maintenance_date": forms.DateInput(),
            "next_maintenance_date": forms.DateInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "size_label": forms.TextInput(attrs={"placeholder": "6'2\" / M"}),
            "wetsuit_thickness": forms.TextInput(attrs={"placeholder": "4/3"}),
        }

    #: (legend, field names) — the template renders the form in these groups.
    FIELDSETS = (
        (_("Identity"), ("asset_code", "category", "name", "brand", "model", "serial_number")),
        (
            _("Dimensions"),
            (
                "size_label",
                "length_cm",
                "width_cm",
                "thickness_cm",
                "volume_litres",
                "wetsuit_thickness",
            ),
        ),
        (
            _("Who may use it"),
            (
                "suitable_min_level",
                "suitable_max_level",
                "min_rider_weight_kg",
                "max_rider_weight_kg",
            ),
        ),
        (_("Condition & storage"), ("condition", "storage_location")),
        (
            _("Purchase"),
            ("purchase_date", "purchase_price", "current_value", "supplier"),
        ),
        (
            _("Rental"),
            (
                "is_rentable",
                "is_lesson_stock",
                "rental_price_hourly",
                "rental_price_daily",
                "rental_price_weekly",
                "deposit_amount",
            ),
        ),
        (_("Service"), ("last_maintenance_date", "next_maintenance_date")),
        (_("Notes"), ("notes",)),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = EquipmentCategory.objects.filter(is_active=True)
        # Indented labels need the pre-walked tree, not the plain queryset order,
        # so the choices are built by hand while validation still uses the queryset.
        tree = [node for node in category_tree() if node.is_active]
        self.fields["category"].choices = [
            (node.pk, f"{'— ' * getattr(node, 'depth', 0)}{node.name}") for node in tree
        ]
        self.fields["asset_code"].required = False
        self.fields["asset_code"].help_text = _("Leave blank to generate the next EQ number.")
        if self.instance.pk:
            # The code is printed on a physical label — changing it orphans the label.
            self.fields["asset_code"].disabled = True
        for name in (
            "length_cm",
            "width_cm",
            "thickness_cm",
            "volume_litres",
            "min_rider_weight_kg",
            "max_rider_weight_kg",
        ):
            self.fields[name].widget.attrs.setdefault("step", "0.1")
        for name in (
            "purchase_price",
            "current_value",
            "rental_price_hourly",
            "rental_price_daily",
            "rental_price_weekly",
            "deposit_amount",
        ):
            self.fields[name].widget.attrs.setdefault("step", "0.01")

    def fieldsets(self):
        """Yield ``(legend, [BoundField, ...])`` for the template."""
        for legend, names in self.FIELDSETS:
            yield legend, [self[name] for name in names if name in self.fields]

    def clean_asset_code(self):
        code = (self.cleaned_data.get("asset_code") or "").strip().upper()
        if code:
            clash = Equipment.all_objects.filter(asset_code=code)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError(_("Another item already uses this asset code."))
        return code


class EquipmentStatusForm(TailwindFormMixin, forms.Form):
    """The inline status change offered on the detail screen."""

    status = forms.ChoiceField(label=_("New status"), choices=())
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        max_length=250,
        widget=forms.TextInput(
            attrs={"placeholder": _("Why is the status changing?")}
        ),
    )

    def __init__(self, *args, equipment: Equipment | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment = equipment
        labels = dict(EquipmentStatus.choices)
        allowed = allowed_next_statuses(equipment) if equipment else ()
        self.fields["status"].choices = [(value, labels[value]) for value in allowed]

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        reason = (cleaned.get("reason") or "").strip()
        if status in STATUS_REASON_REQUIRED and not reason:
            self.add_error("reason", _("Give a reason for this change."))
        cleaned["reason"] = reason
        return cleaned


class EquipmentCategoryForm(BaseModelForm):
    class Meta:
        model = EquipmentCategory
        fields = ("code", "name", "parent", "icon", "sort_order", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = EquipmentCategory.objects.all()
        if self.instance.pk:
            # A category may not become a child of itself or of its own children.
            queryset = queryset.exclude(pk__in=self.instance.descendant_ids)
        self.fields["parent"].queryset = queryset
        self.fields["parent"].required = False
        self.fields["icon"].help_text = _(
            "Name of a vendored Lucide icon, e.g. waves, package, camera."
        )

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().lower()


class EquipmentPhotoForm(BaseModelForm):
    class Meta:
        model = EquipmentPhoto
        fields = ("image", "caption", "is_primary", "taken_at")
        widgets = {"taken_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class EquipmentImportForm(TailwindFormMixin, forms.Form):
    """Step one of the CSV import: choose the file."""

    file = forms.FileField(
        label=_("CSV file"),
        help_text=_("UTF-8 CSV. Download the template to see the expected columns."),
        validators=[validate_document_upload],
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (getattr(uploaded, "name", "") or "").lower()
        if not name.endswith(".csv"):
            raise forms.ValidationError(_("Upload a .csv file."))
        return uploaded


class BoardAdvisorForm(TailwindFormMixin, forms.Form):
    """Ask the fleet which board suits a rider."""

    weight_kg = forms.DecimalField(
        label=_("Rider weight (kg)"),
        min_value=10,
        max_value=250,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"step": "0.5"}),
    )
    level = forms.ChoiceField(
        label=_("Surf level"), choices=SurfLevel.choices, initial=SurfLevel.FIRST_TIME
    )


class WetsuitAdvisorForm(TailwindFormMixin, forms.Form):
    """Ask the fleet which suit suits the water."""

    water_temp_c = forms.DecimalField(
        label=_("Water temperature (°C)"),
        min_value=-2,
        max_value=40,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"step": "0.5"}),
    )
    size = forms.CharField(
        label=_("Size"),
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "M / MT / L"}),
    )


#: Choices reused by the list toolbar.
STATUS_CHOICES = EquipmentStatus.choices
CONDITION_CHOICES = EquipmentCondition.choices
