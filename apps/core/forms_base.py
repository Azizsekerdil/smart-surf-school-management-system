"""Shared form building blocks.

``TailwindFormMixin`` lives in ``apps.accounts.forms`` (it is needed by the auth
forms, which load before anything else). It is re-exported here so business
modules can import it from ``apps.core`` without depending on the accounts app.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import (  # noqa: F401  (re-export)
    CHECKBOX_CLASS,
    INPUT_CLASS,
    SELECT_CLASS,
    TailwindFormMixin,
)

__all__ = [
    "TailwindFormMixin",
    "INPUT_CLASS",
    "SELECT_CLASS",
    "CHECKBOX_CLASS",
    "BaseModelForm",
    "DateRangeFilterForm",
]


class BaseModelForm(TailwindFormMixin, forms.ModelForm):
    """Project-standard ModelForm: consistent styling for every module."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class DateRangeFilterForm(TailwindFormMixin, forms.Form):
    """The shared date filter used on every list and dashboard screen."""

    from apps.core.utils import RANGE_CHOICES  # noqa: PLC0415 - avoids a cycle at import time

    range = forms.ChoiceField(
        label=_("Period"), choices=RANGE_CHOICES, required=False, initial="30"
    )
    start = forms.DateField(label=_("From"), required=False, widget=forms.DateInput())
    end = forms.DateField(label=_("To"), required=False, widget=forms.DateInput())

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            raise forms.ValidationError(_("The end date must not be before the start date."))
        return cleaned
