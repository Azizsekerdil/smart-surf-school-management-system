"""Forms for taking, filtering, confirming and pruning backups."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import TailwindFormMixin

from .models import BackupRecord, BackupScope, BackupStatus, BackupType


class BackupCreateForm(TailwindFormMixin, forms.Form):
    """The "create backup now" control on the list screen."""

    scope = forms.ChoiceField(
        label=_("What to back up"),
        choices=BackupScope.choices,
        initial=BackupScope.FULL,
        help_text=_(
            "A full backup contains the database, the uploaded files and a "
            "configuration manifest. Private uploads are never included."
        ),
    )
    notes = forms.CharField(
        label=_("Note"),
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={"placeholder": _("Why this backup is being taken — optional")}
        ),
    )


class BackupFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the backup table."""

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Backup code or note…")}),
    )
    backup_type = forms.ChoiceField(
        label=_("Type"),
        required=False,
        choices=[("", _("Any type"))] + list(BackupType.choices),
    )
    scope = forms.ChoiceField(
        label=_("Scope"),
        required=False,
        choices=[("", _("Any scope"))] + list(BackupScope.choices),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("Any status"))] + list(BackupStatus.choices),
    )


class RestoreConfirmationForm(TailwindFormMixin, forms.Form):
    """The last gate before live data is overwritten.

    The operator must type the backup code by hand. Nothing is pre-filled and
    nothing is case-insensitive: the point of the exercise is that a person
    reads the code off the screen and copies it deliberately.
    """

    confirmation_text = forms.CharField(
        label=_("Type the backup code to confirm"),
        max_length=64,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocorrect": "off",
                "autocapitalize": "off",
                "spellcheck": "false",
                # Drives the client-side gate on the confirmation screen. The
                # server re-checks the same rule; this only stops a misclick.
                "x-model": "typed",
            }
        ),
    )
    understood = forms.BooleanField(
        label=_(
            "I understand that current data will be replaced and cannot be "
            "recovered except from a backup."
        ),
        required=True,
        widget=forms.CheckboxInput(attrs={"x-model": "understood"}),
    )
    notes = forms.CharField(
        label=_("Reason for this restore"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, backup: BackupRecord | None = None, **kwargs):
        self.backup = backup
        super().__init__(*args, **kwargs)
        if backup is not None:
            self.fields["confirmation_text"].widget.attrs["placeholder"] = backup.backup_code

    def clean_confirmation_text(self) -> str:
        typed = (self.cleaned_data.get("confirmation_text") or "").strip()
        if self.backup is not None and typed != self.backup.backup_code:
            raise forms.ValidationError(
                _("That is not the backup code. Type %(code)s exactly as shown.")
                % {"code": self.backup.backup_code}
            )
        return typed


class RetentionPolicyForm(TailwindFormMixin, forms.Form):
    """How many scheduled backups of each cadence to keep."""

    daily = forms.IntegerField(
        label=_("Daily backups to keep"),
        min_value=1,
        max_value=365,
        help_text=_("Older daily backups are removed by the nightly sweep."),
    )
    weekly = forms.IntegerField(
        label=_("Weekly backups to keep"),
        min_value=1,
        max_value=365,
    )
    monthly = forms.IntegerField(
        label=_("Monthly backups to keep"),
        min_value=1,
        max_value=365,
        help_text=_("Twelve monthly copies cover a full season-to-season comparison."),
    )
