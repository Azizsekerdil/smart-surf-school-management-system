"""One small form per wizard step.

Each form edits the same :class:`~apps.onboarding.models.OnboardingState` row,
which is why they are ``ModelForm`` subclasses over a subset of fields rather
than one long form split across pages: a half-finished setup is saved as it
goes, and closing the browser at step four loses nothing.

Every field is optional. The wizard is skippable at any point, so a form must
never be the thing that blocks somebody from reaching the end.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, available_timezones

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import Language
from apps.core.forms_base import TailwindFormMixin

from .models import CURRENCY_CHOICES, OnboardingState


class OnboardingStepForm(TailwindFormMixin, forms.ModelForm):
    """Base for every step form: same styling, same model, same row."""

    class Meta:
        model = OnboardingState
        fields: list[str] = []

    def has_any_answer(self) -> bool:
        """Did the operator actually fill anything in on this step?"""
        return any(self.cleaned_data.get(name) not in (None, "", False) for name in self.fields)


class BusinessInfoForm(OnboardingStepForm):
    """Step 2 — who this installation belongs to and what day it is there."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["school_name", "timezone"]
        labels = {
            "school_name": _("School name"),
            "timezone": _("Timezone"),
        }
        help_texts = {
            "school_name": _("Appears on invoices, e-mails and every printed manifest."),
            "timezone": _(
                "Decides what “today” means for lesson times, the dashboard and every "
                "scheduled job. Use the timezone the school physically operates in."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_name"].required = False
        self.fields["timezone"].required = False
        self.fields["school_name"].widget.attrs.update(
            {"placeholder": _("e.g. Alaçatı Surf Academy"), "autofocus": True}
        )
        # A datalist of the common choices, but any IANA name is accepted.
        self.fields["timezone"].widget.attrs.update(
            {"list": "onboarding-timezones", "placeholder": "Europe/Istanbul"}
        )

    def clean_timezone(self) -> str:
        value = (self.cleaned_data.get("timezone") or "").strip()
        if not value:
            return ""
        if value not in available_timezones():
            raise forms.ValidationError(
                _("“%(value)s” is not a known timezone. Use an IANA name such as Europe/Istanbul.")
                % {"value": value}
            )
        # Constructing it proves the tz database on this machine can load it.
        ZoneInfo(value)
        return value


class LanguageForm(OnboardingStepForm):
    """Step 3 — the language new users and outgoing messages default to."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["default_language"]
        labels = {"default_language": _("Default language")}
        help_texts = {
            "default_language": _(
                "Used for new user accounts and for customers who have no language of "
                "their own recorded. Everyone can still switch at any time."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["default_language"]
        field.required = False
        # The interface itself ships in Turkish and English only.
        field.choices = [("", _("Choose a language"))] + [
            (value, label)
            for value, label in Language.choices
            if value in {Language.TURKISH, Language.ENGLISH}
        ]


class CurrencyForm(OnboardingStepForm):
    """Step 4 — the currency every amount in the system is expressed in."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["currency"]
        labels = {"currency": _("Currency")}
        help_texts = {
            "currency": _(
                "Every price, invoice and report uses this. Changing it later does not "
                "convert existing figures — it only changes the symbol in front of them."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].required = False
        self.fields["currency"].choices = [("", _("Choose a currency"))] + list(CURRENCY_CHOICES)


class LocationForm(OnboardingStepForm):
    """Step 5 — the break the school teaches at."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["primary_spot_name", "latitude", "longitude", "beach_facing_deg"]
        labels = {
            "primary_spot_name": _("Primary surf spot"),
            "latitude": _("Latitude"),
            "longitude": _("Longitude"),
            "beach_facing_deg": _("Beach facing (°)"),
        }
        help_texts = {
            "primary_spot_name": _(
                "The break used whenever a lesson, camp day or rental does not name one."
            ),
            "beach_facing_deg": _(
                "0 = north, 90 = east, 180 = south, 270 = west. Stand on the sand looking "
                "at the water: that is the bearing. Wind is classified against it, so the "
                "surf score and half the safety logic depend on it."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].required = False
        self.fields["primary_spot_name"].widget.attrs.update({"autofocus": True})
        self.fields["latitude"].widget.attrs.update({"step": "any", "placeholder": "38.28"})
        self.fields["longitude"].widget.attrs.update({"step": "any", "placeholder": "26.37"})
        self.fields["beach_facing_deg"].widget.attrs.update(
            {"step": "any", "min": "0", "max": "360", "placeholder": "180"}
        )

    def clean(self):
        cleaned = super().clean()
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")
        name = (cleaned.get("primary_spot_name") or "").strip()
        facing = cleaned.get("beach_facing_deg")

        if (latitude is None) != (longitude is None):
            raise forms.ValidationError(
                _("Enter both a latitude and a longitude, or neither.")
            )
        if facing is not None and not (0.0 <= float(facing) <= 360.0):
            self.add_error("beach_facing_deg", _("Enter a bearing between 0 and 360 degrees."))
        if name and latitude is None:
            self.add_error(
                "latitude",
                _("A surf spot cannot be created without coordinates."),
            )
        return cleaned


class AISetupForm(OnboardingStepForm):
    """Step 7 — an acknowledgement, not a place to type an API key."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["ai_configured"]
        labels = {"ai_configured": _("AI assistant is configured")}
        help_texts = {
            "ai_configured": _(
                "Tick this once a provider has been set up in the AI Control Center. "
                "Keys are never entered here — they come from the server environment."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ai_configured"].required = False


class BackupSetupForm(OnboardingStepForm):
    """Step 8 — an acknowledgement that somebody owns the backup."""

    class Meta(OnboardingStepForm.Meta):
        fields = ["backup_configured"]
        labels = {"backup_configured": _("Backups are set up and copied off this machine")}
        help_texts = {
            "backup_configured": _(
                "A backup sitting next to the database protects you from a mistake, not "
                "from a fire or ransomware. Tick this only when a copy leaves the machine."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["backup_configured"].required = False
