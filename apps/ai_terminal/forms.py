from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import TailwindFormMixin


class CommandForm(TailwindFormMixin, forms.Form):
    command = forms.CharField(
        label=_("Command"),
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "placeholder": "git status",
                "autocomplete": "off",
                "spellcheck": "false",
                "class": "font-mono",
            }
        ),
    )
    rationale = forms.CharField(label=_("Reason"), required=False, max_length=500)


class ApprovalForm(TailwindFormMixin, forms.Form):
    note = forms.CharField(
        label=_("Note"),
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": _("Optional note for the audit log")}),
    )
    edited_command = forms.CharField(
        label=_("Edit before approving"),
        required=False,
        max_length=2000,
        widget=forms.TextInput(attrs={"class": "font-mono"}),
        help_text=_("An edited command is re-checked against the policy before it can run."),
    )


class AgentRequestForm(TailwindFormMixin, forms.Form):
    request_text = forms.CharField(
        label=_("What should the agent do?"),
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": _(
                    "e.g. Add a monthly calendar to the booking screen, "
                    "or write tests for the rental late-fee calculation"
                ),
            }
        ),
        max_length=4000,
    )
    context_files = forms.CharField(
        label=_("Files to look at"),
        required=False,
        max_length=1000,
        widget=forms.TextInput(
            attrs={
                "placeholder": "apps/bookings/views.py, templates/bookings/booking_list.html",
                "class": "font-mono",
            }
        ),
        help_text=_("Comma-separated. Leave empty to let the agent choose."),
    )

    def cleaned_context_files(self) -> list[str]:
        raw = self.cleaned_data.get("context_files") or ""
        return [part.strip() for part in raw.split(",") if part.strip()][:6]
