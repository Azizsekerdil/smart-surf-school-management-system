"""Forms for surf conditions.

The manual-entry form exists for the case the provider architecture cannot
cover: the internet is down, or the model disagrees with the water. A coach
standing on the beach is a better sensor than a grid cell 3 km offshore, and a
reading they log is marked ``source=MANUAL`` so nobody later mistakes it for
model output.
"""

from __future__ import annotations

from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import SurfLevel, TideState
from apps.core.forms_base import TailwindFormMixin

from .models import SurfCondition

#: Physical sanity limits. These are not the safety thresholds — they only stop
#: a typo ("120 m") entering the record as if it were a measurement.
MAX_PLAUSIBLE_WAVE_M = 25.0
MAX_PLAUSIBLE_WIND_KMH = 250.0
MAX_PLAUSIBLE_PERIOD_S = 30.0


class SurfConditionForm(TailwindFormMixin, forms.ModelForm):
    """Log a reading by hand, or correct one."""

    class Meta:
        model = SurfCondition
        fields = (
            "spot",
            "recorded_at",
            "wave_height_m",
            "wave_period_s",
            "wave_direction_deg",
            "swell_height_m",
            "swell_period_s",
            "swell_direction_deg",
            "wind_speed_kmh",
            "wind_gust_kmh",
            "wind_direction_deg",
            "tide_state",
            "air_temperature_c",
            "water_temperature_c",
            "weather_description",
            "precipitation_mm",
            "visibility_km",
        )
        widgets = {
            "recorded_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "wave_height_m": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "wave_period_s": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "wave_direction_deg": forms.NumberInput(attrs={"step": "1", "min": "0", "max": "360"}),
            "swell_height_m": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "swell_period_s": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "swell_direction_deg": forms.NumberInput(
                attrs={"step": "1", "min": "0", "max": "360"}
            ),
            "wind_speed_kmh": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "wind_gust_kmh": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "wind_direction_deg": forms.NumberInput(attrs={"step": "1", "min": "0", "max": "360"}),
            "air_temperature_c": forms.NumberInput(attrs={"step": "0.1"}),
            "water_temperature_c": forms.NumberInput(attrs={"step": "0.1"}),
            "precipitation_mm": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "visibility_km": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        from apps.locations.models import SurfSpot

        self.fields["spot"].queryset = SurfSpot.objects.filter(is_active=True).order_by(
            "-is_primary", "name"
        )
        self.fields["recorded_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        if not self.instance.pk:
            self.fields["recorded_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        self.fields["tide_state"].help_text = _(
            "What you saw at the water, not a model output."
        )

    # -- field validation --------------------------------------------------
    def clean_recorded_at(self):
        moment = self.cleaned_data.get("recorded_at")
        if moment and moment > timezone.now() + timedelta(minutes=15):
            raise forms.ValidationError(
                _("A logged reading describes something you observed — it cannot be in the future.")
            )
        return moment

    def _check_range(self, field: str, maximum: float, message) -> None:
        value = self.cleaned_data.get(field)
        if value is None:
            return
        if value < 0 or value > maximum:
            self.add_error(field, message)

    def _check_bearing(self, field: str) -> None:
        value = self.cleaned_data.get(field)
        if value is not None and not (0.0 <= float(value) <= 360.0):
            self.add_error(field, _("Enter a bearing between 0 and 360 degrees."))

    def clean(self):
        cleaned = super().clean()

        self._check_range(
            "wave_height_m",
            MAX_PLAUSIBLE_WAVE_M,
            _("Enter a wave height between 0 and %(max)s m.") % {"max": MAX_PLAUSIBLE_WAVE_M},
        )
        self._check_range(
            "swell_height_m",
            MAX_PLAUSIBLE_WAVE_M,
            _("Enter a swell height between 0 and %(max)s m.") % {"max": MAX_PLAUSIBLE_WAVE_M},
        )
        for field in ("wave_period_s", "swell_period_s"):
            self._check_range(
                field,
                MAX_PLAUSIBLE_PERIOD_S,
                _("Enter a period between 0 and %(max)s s.") % {"max": MAX_PLAUSIBLE_PERIOD_S},
            )
        for field in ("wind_speed_kmh", "wind_gust_kmh"):
            self._check_range(
                field,
                MAX_PLAUSIBLE_WIND_KMH,
                _("Enter a wind speed between 0 and %(max)s km/h.")
                % {"max": MAX_PLAUSIBLE_WIND_KMH},
            )
        for field in ("wave_direction_deg", "swell_direction_deg", "wind_direction_deg"):
            self._check_bearing(field)

        gust = cleaned.get("wind_gust_kmh")
        speed = cleaned.get("wind_speed_kmh")
        if gust is not None and speed is not None and gust < speed:
            self.add_error(
                "wind_gust_kmh", _("A gust cannot be weaker than the average wind speed.")
            )

        if cleaned.get("wave_height_m") is None and cleaned.get("wind_speed_kmh") is None:
            # An empty reading would score nothing and mean nothing.
            self.add_error(
                "wave_height_m",
                _("Record at least a wave height or a wind speed."),
            )

        spot = cleaned.get("spot")
        moment = cleaned.get("recorded_at")
        if spot and moment:
            clash = SurfCondition.all_objects.filter(
                spot=spot, recorded_at=moment, is_forecast=False
            ).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(
                    "recorded_at",
                    _("A reading for this spot at this exact time already exists."),
                )
        return cleaned

    def save(self, commit: bool = True):
        condition = super().save(commit=False)
        condition.is_forecast = False
        condition.source = SurfCondition.Source.MANUAL
        condition.provider = "manual"
        if self.user is not None and getattr(self.user, "is_authenticated", False):
            if condition.pk is None:
                condition.created_by = self.user
            condition.updated_by = self.user
        if commit:
            condition.save()
        return condition


class ConditionFilterForm(TailwindFormMixin, forms.Form):
    """The picker above the dashboard and the reading history."""

    SOURCE_CHOICES = (
        ("", _("Any source")),
        (SurfCondition.Source.PROVIDER, _("Weather provider")),
        (SurfCondition.Source.MANUAL, _("Entered by staff")),
    )

    spot = forms.ChoiceField(label=_("Surf spot"), required=False, choices=())
    level = forms.ChoiceField(
        label=_("Surf level"),
        required=False,
        choices=[("", _("All levels"))] + list(SurfLevel.choices),
    )
    tide = forms.ChoiceField(
        label=_("Tide"),
        required=False,
        choices=[("", _("Any tide"))] + list(TideState.choices),
    )
    source = forms.ChoiceField(label=_("Source"), required=False, choices=SOURCE_CHOICES)

    def __init__(self, *args, spot_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(spot_choices or [])
        self.fields["spot"].choices = [("", _("All spots"))] + choices
