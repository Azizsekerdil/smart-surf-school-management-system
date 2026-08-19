"""Forms for the Help Center.

Only search is a form: article content is authored in the Django admin, which
already gives editors a rich enough surface and keeps the read-only reader UI
free of write paths.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import TailwindFormMixin

from .models import HelpCategory


class HelpSearchForm(TailwindFormMixin, forms.Form):
    """The search bar shown on the Help Center home and results screens."""

    q = forms.CharField(
        label=_("Search the manual"),
        required=False,
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "placeholder": _("What are you trying to do?"),
                "autocomplete": "off",
            }
        ),
    )
    category = forms.ChoiceField(
        label=_("Section"),
        required=False,
        choices=(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", _("All sections"))] + [
            (category.code, category.name)
            for category in HelpCategory.objects.filter(is_active=True)
        ]

    def clean_q(self) -> str:
        return (self.cleaned_data.get("q") or "").strip()
