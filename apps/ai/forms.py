from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms_base import BaseModelForm, TailwindFormMixin

from .models import AIProviderConfig, RagDocument
from .router import RoutingMode


class ChatForm(TailwindFormMixin, forms.Form):
    message = forms.CharField(
        label=_("Message"),
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": _("Ask about lessons, bookings, revenue, equipment…"),
                "x-on:keydown.enter.prevent": "$event.shiftKey ? null : $el.form.requestSubmit()",
            }
        ),
        max_length=8000,
    )
    routing_mode = forms.ChoiceField(
        label=_("AI mode"), choices=RoutingMode.CHOICES, required=False, initial=RoutingMode.LOCAL_ONLY
    )
    use_rag = forms.BooleanField(
        label=_("Use knowledge base"), required=False, initial=True
    )


class RagDocumentForm(BaseModelForm):
    class Meta:
        model = RagDocument
        fields = ("title", "source_type", "language", "content", "file", "source_url", "is_active")
        widgets = {
            "content": forms.Textarea(attrs={"rows": 14}),
        }
        help_texts = {
            "content": _(
                "Plain text the assistant may quote. Do not paste passwords, API keys "
                "or payment details here."
            ),
        }


class AIProviderConfigForm(BaseModelForm):
    """Non-secret provider settings.

    API keys are intentionally absent: they live in the environment so a database
    dump can never expose them.
    """

    class Meta:
        model = AIProviderConfig
        fields = ("is_enabled", "base_url_override", "model_overrides", "monthly_budget_usd")
        widgets = {"model_overrides": forms.Textarea(attrs={"rows": 6})}
        help_texts = {
            "base_url_override": _("Leave empty to use the value from the environment."),
            "model_overrides": _('JSON, e.g. {"assistant": "nvidia/nemotron-3-super-120b-a12b"}'),
            "monthly_budget_usd": _("0 disables the limit."),
        }

    def clean_model_overrides(self):
        value = self.cleaned_data.get("model_overrides")
        if value in (None, "", {}):
            return {}
        if not isinstance(value, dict):
            raise forms.ValidationError(_("Model overrides must be a JSON object."))
        for key, model in value.items():
            if not isinstance(key, str) or not isinstance(model, str):
                raise forms.ValidationError(_("Both role and model must be text values."))
        return value
