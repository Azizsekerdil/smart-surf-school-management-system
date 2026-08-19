"""Forms for notification preferences and staff broadcasts."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import STAFF_ROLES, Role
from apps.core.forms_base import CHECKBOX_CLASS, TailwindFormMixin

from .models import (
    MAX_RENDERED_BODY,
    MAX_RENDERED_TITLE,
    NotificationCategory,
    NotificationLevel,
    NotificationPreference,
)


class NotificationPreferenceForm(TailwindFormMixin, forms.ModelForm):
    """A user's own delivery rules."""

    categories_muted = forms.MultipleChoiceField(
        label=_("Mute these categories"),
        choices=NotificationCategory.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Muted categories are silenced everywhere, including e-mail."),
    )

    class Meta:
        model = NotificationPreference
        fields = (
            "in_app_enabled",
            "email_enabled",
            "categories_muted",
            "quiet_hours_start",
            "quiet_hours_end",
        )
        widgets = {
            "quiet_hours_start": forms.TimeInput(attrs={"type": "time"}),
            "quiet_hours_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # CheckboxSelectMultiple is not a Select widget, so the shared mixin
        # would style it as a text input.
        self.fields["categories_muted"].widget.attrs["class"] = CHECKBOX_CLASS

    def clean_categories_muted(self) -> list[str]:
        chosen = self.cleaned_data.get("categories_muted") or []
        return sorted({value for value in chosen if value in NotificationCategory.values})

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("quiet_hours_start")
        end = cleaned.get("quiet_hours_end")
        if bool(start) != bool(end):
            raise forms.ValidationError(
                _("Set both a start and an end time for quiet hours, or neither.")
            )
        if start and end and start == end:
            raise forms.ValidationError(
                _("Quiet hours must start and end at different times.")
            )
        return cleaned


class BroadcastForm(TailwindFormMixin, forms.Form):
    """Send one message to every active holder of the selected roles.

    Used for the things a surf school genuinely needs to push at people:
    a beach closure, a shift change, a storm rolling in.
    """

    roles = forms.MultipleChoiceField(
        label=_("Send to roles"),
        choices=[(value, label) for value, label in Role.choices if value in STAFF_ROLES],
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Every active user with one of these roles receives the message."),
    )
    category = forms.ChoiceField(
        label=_("Category"),
        choices=NotificationCategory.choices,
        initial=NotificationCategory.SYSTEM,
    )
    level = forms.ChoiceField(
        label=_("Level"),
        choices=NotificationLevel.choices,
        initial=NotificationLevel.INFO,
        help_text=_("Warnings and errors are also e-mailed, subject to quiet hours."),
    )
    title = forms.CharField(label=_("Title"), max_length=MAX_RENDERED_TITLE)
    body = forms.CharField(
        label=_("Message"),
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=MAX_RENDERED_BODY,
        required=False,
    )
    link_url = forms.CharField(
        label=_("Link"),
        max_length=500,
        required=False,
        help_text=_("Optional path inside the application, for example /bookings/12/"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].widget.attrs["class"] = CHECKBOX_CLASS

    def clean_link_url(self) -> str:
        link = (self.cleaned_data.get("link_url") or "").strip()
        if not link:
            return ""
        if not link.startswith("/") or link.startswith("//") or "\\" in link:
            raise forms.ValidationError(
                _("Enter a path inside this application, starting with a single “/”.")
            )
        return link

    def clean_roles(self) -> list[str]:
        roles = [value for value in (self.cleaned_data.get("roles") or []) if value in STAFF_ROLES]
        if not roles:
            raise forms.ValidationError(_("Choose at least one role."))
        return roles
