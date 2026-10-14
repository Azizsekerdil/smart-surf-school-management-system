"""Forms for authentication and user administration."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.utils.translation import gettext_lazy as _

from .constants import MODULE_LABELS, Role, all_capabilities
from .models import User

# Tailwind classes reused across every form widget.
INPUT_CLASS = (
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 "
    "placeholder-slate-400 shadow-xs transition focus:border-sky-500 focus:outline-hidden "
    "focus:ring-2 focus:ring-sky-500/30 dark:border-slate-600 dark:bg-slate-800 "
    "dark:text-slate-100 dark:placeholder-slate-500"
)
SELECT_CLASS = INPUT_CLASS
CHECKBOX_CLASS = (
    "h-4 w-4 rounded-sm border-slate-300 text-sky-600 focus:ring-sky-500 dark:border-slate-600"
)


class TailwindFormMixin:
    """Applies consistent styling to every widget of a form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = f"{existing} {CHECKBOX_CLASS}".strip()
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs["class"] = f"{existing} {SELECT_CLASS}".strip()
            elif isinstance(widget, forms.FileInput):
                widget.attrs["class"] = (
                    f"{existing} block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg "
                    "file:border-0 file:bg-sky-50 file:px-4 file:py-2 file:text-sm "
                    "file:font-medium file:text-sky-700 hover:file:bg-sky-100"
                ).strip()
            else:
                widget.attrs["class"] = f"{existing} {INPUT_CLASS}".strip()
            if isinstance(widget, forms.DateInput):
                widget.input_type = "date"
            elif isinstance(widget, forms.TimeInput):
                widget.input_type = "time"


class LoginForm(TailwindFormMixin, AuthenticationForm):
    username = forms.CharField(
        label=_("Username or e-mail"),
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    remember_me = forms.BooleanField(label=_("Keep me signed in"), required=False)

    error_messages = {
        "invalid_login": _("Incorrect username/e-mail or password."),
        "inactive": _("This account is disabled. Contact your manager."),
    }


class UserCreateForm(TailwindFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "phone",
            "job_title",
            "employee_id",
            "language",
            "photo",
        )

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("A user with this e-mail address already exists."))
        return email


class UserUpdateForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "role",
            "phone",
            "job_title",
            "employee_id",
            "language",
            "photo",
            "is_active",
            "notes",
        )
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        # Only a Super Admin may create or promote another Super Admin.
        if editor is not None and not editor.is_super_admin:
            self.fields["role"].choices = [
                (value, label) for value, label in Role.choices if value != Role.SUPER_ADMIN
            ]

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("A user with this e-mail address already exists."))
        return email

    def clean_is_active(self):
        is_active = self.cleaned_data.get("is_active")
        if not is_active and self.editor is not None and self.instance.pk == self.editor.pk:
            raise forms.ValidationError(_("You cannot deactivate your own account."))
        return is_active


class CapabilityOverrideForm(forms.Form):
    """Grant or revoke individual capabilities on top of a user's role."""

    def __init__(self, *args, user: User | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user = user
        granted = set(user.extra_capabilities or []) if user else set()
        denied = set(user.denied_capabilities or []) if user else set()

        by_module: dict[str, list[str]] = {}
        for capability in sorted(all_capabilities()):
            module = capability.split(".", 1)[0]
            by_module.setdefault(module, []).append(capability)

        self.grouped_fields: list[tuple[str, object, list[str]]] = []
        for module, capabilities in by_module.items():
            names = []
            for capability in capabilities:
                grant_name = f"grant__{capability}"
                deny_name = f"deny__{capability}"
                self.fields[grant_name] = forms.BooleanField(
                    required=False, label=capability, initial=capability in granted
                )
                self.fields[deny_name] = forms.BooleanField(
                    required=False, label=capability, initial=capability in denied
                )
                names.append(capability)
            self.grouped_fields.append((module, MODULE_LABELS.get(module, module), names))

    def save(self) -> User:
        grants, denials = [], []
        for key, value in self.cleaned_data.items():
            if not value:
                continue
            if key.startswith("grant__"):
                grants.append(key.removeprefix("grant__"))
            elif key.startswith("deny__"):
                denials.append(key.removeprefix("deny__"))
        self.target_user.extra_capabilities = sorted(set(grants))
        self.target_user.denied_capabilities = sorted(set(denials))
        self.target_user.save(update_fields=["extra_capabilities", "denied_capabilities"])
        return self.target_user


class ProfileForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "photo", "language")


class SurfPasswordChangeForm(TailwindFormMixin, PasswordChangeForm):
    """Password change with project styling."""


class SurfSetPasswordForm(TailwindFormMixin, SetPasswordForm):
    """Password reset confirmation with project styling."""
