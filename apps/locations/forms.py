"""Forms for surf spots and their hazards."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import BreakType, Severity, SurfLevel, TideState, level_rank
from apps.core.forms_base import TailwindFormMixin

from .models import TIDE_CYCLE, SpotHazard, SurfSpot


class SurfSpotForm(TailwindFormMixin, forms.ModelForm):
    """Create / edit a surf spot.

    ``code`` and ``slug`` are intentionally absent: both are generated so two
    operators typing at the same time cannot produce a duplicate reference.
    """

    class Meta:
        model = SurfSpot
        fields = (
            "name",
            "description",
            "latitude",
            "longitude",
            "altitude",
            "beach_facing_deg",
            "break_type",
            "bottom_type",
            "min_level",
            "max_level",
            "ideal_tide",
            "ideal_wind",
            "ideal_swell_direction_deg",
            "capacity",
            "is_active",
            "is_primary",
            "parking_info",
            "access_notes",
            "photo",
            "lifeguard_on_duty",
            "nearest_hospital",
            "nearest_hospital_phone",
            "emergency_notes",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "parking_info": forms.Textarea(attrs={"rows": 2}),
            "access_notes": forms.Textarea(attrs={"rows": 3}),
            "emergency_notes": forms.Textarea(attrs={"rows": 3}),
            "latitude": forms.NumberInput(attrs={"step": "0.000001", "inputmode": "decimal"}),
            "longitude": forms.NumberInput(attrs={"step": "0.000001", "inputmode": "decimal"}),
            "altitude": forms.NumberInput(attrs={"step": "0.1"}),
            "beach_facing_deg": forms.NumberInput(
                attrs={"step": "1", "min": "0", "max": "360"}
            ),
            "ideal_swell_direction_deg": forms.NumberInput(
                attrs={"step": "1", "min": "0", "max": "360"}
            ),
            "capacity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "nearest_hospital_phone": forms.TextInput(
                attrs={"placeholder": "+90 555 123 45 67", "inputmode": "tel"}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["ideal_tide"].choices = [
            (value, label) for value, label in TideState.choices if value in TIDE_CYCLE
        ]
        if self.instance.pk and self.instance.is_primary:
            # The flag can only be moved to another spot, never simply removed —
            # otherwise the school ends up with no default at all.
            self.fields["is_primary"].disabled = True
            self.fields["is_primary"].help_text = _(
                "This is the default spot. Promote another spot to move the flag."
            )

    def clean_name(self) -> str:
        name = (self.cleaned_data.get("name") or "").strip()
        duplicate = SurfSpot.all_objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(_("A spot with this name already exists."))
        return name

    def clean(self):
        cleaned = super().clean()
        min_level = cleaned.get("min_level")
        max_level = cleaned.get("max_level")
        if min_level and max_level and level_rank(min_level) > level_rank(max_level):
            self.add_error("max_level", _("The maximum level must not be below the minimum level."))

        is_active = cleaned.get("is_active")
        is_primary = cleaned.get("is_primary")
        if is_primary and not is_active:
            self.add_error("is_active", _("The default spot must stay active."))

        if self.instance.pk and self.instance.is_primary and is_active is False:
            self.add_error(
                "is_active",
                _("Promote another spot to default before deactivating this one."),
            )

        lifeguard = cleaned.get("lifeguard_on_duty")
        hospital = (cleaned.get("nearest_hospital") or "").strip()
        if not lifeguard and not hospital:
            # Not fatal, but an unpatrolled break with no hospital on file is a
            # gap the operator should see now rather than during an incident.
            self.add_error(
                "nearest_hospital",
                _("Unpatrolled spots must record the nearest hospital."),
            )
        return cleaned


class SpotHazardForm(TailwindFormMixin, forms.ModelForm):
    """Record or amend a hazard at a spot."""

    class Meta:
        model = SpotHazard
        fields = (
            "name",
            "severity",
            "description",
            "is_active",
            "applies_from_tide",
            "applies_to_tide",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, spot: SurfSpot | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.spot = spot or getattr(self.instance, "spot", None)
        tide_choices = [("", _("Any tide"))] + [
            (value, label) for value, label in TideState.choices if value in TIDE_CYCLE
        ]
        self.fields["applies_from_tide"].choices = tide_choices
        self.fields["applies_to_tide"].choices = tide_choices

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("applies_from_tide")
        end = cleaned.get("applies_to_tide")
        if start and not end:
            cleaned["applies_to_tide"] = start
        if end and not start:
            cleaned["applies_from_tide"] = end

        if cleaned.get("severity") == Severity.CRITICAL and not (
            cleaned.get("description") or ""
        ).strip():
            self.add_error(
                "description",
                _("A critical hazard closes the spot — describe it for the staff briefing."),
            )
        return cleaned

    def save(self, commit: bool = True):
        hazard = super().save(commit=False)
        if self.spot is not None:
            hazard.spot = self.spot
        if commit:
            hazard.save()
        return hazard


class SpotFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the spot list."""

    STATUS_CHOICES = (
        ("active", _("Active")),
        ("inactive", _("Archived")),
        ("all", _("All")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Name, code or notes…")}),
    )
    level = forms.ChoiceField(
        label=_("Suits level"),
        required=False,
        choices=[("", _("Any level"))] + list(SurfLevel.choices),
    )
    break_type = forms.ChoiceField(
        label=_("Break type"),
        required=False,
        choices=[("", _("Any break"))] + list(BreakType.choices),
    )
    lifeguard = forms.ChoiceField(
        label=_("Lifeguard"),
        required=False,
        choices=(("", _("Any")), ("yes", _("Patrolled")), ("no", _("Unpatrolled"))),
    )
    status = forms.ChoiceField(label=_("Status"), required=False, choices=STATUS_CHOICES)
