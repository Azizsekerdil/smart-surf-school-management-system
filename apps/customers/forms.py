"""Customer forms."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import BookingSource, Gender
from apps.core.forms_base import TailwindFormMixin
from apps.core.models import Document, Note, Tag

from .models import Customer, CustomerTag, normalise_phone
from .selectors import customers_matching_contact


class CustomerForm(TailwindFormMixin, forms.ModelForm):
    """Full customer record — used by the create and update screens."""

    tags = forms.ModelMultipleChoiceField(
        label=_("Tags"),
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
        help_text=_("Used for segments and campaigns."),
    )
    allow_duplicate = forms.BooleanField(
        label=_("Save anyway — this is a different person"),
        required=False,
        help_text=_("Tick only after checking the matching record below."),
    )

    class Meta:
        model = Customer
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "photo",
            "birth_date",
            "gender",
            "nationality",
            "preferred_language",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relation",
            "source",
            "marketing_consent",
            "is_active",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "notes",
        )
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "address_line1": forms.TextInput(),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "+90 555 123 45 67"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].widget.attrs["autofocus"] = True
        self.fields["nationality"].widget.attrs["placeholder"] = "TR"
        self.fields["gender"].choices = [("", _("Not specified"))] + list(Gender.choices)
        if self.instance.pk:
            self.fields["tags"].initial = self.instance.tags.all()
        self.duplicate_match: Customer | None = None
        self._selected_tags: list = []

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_phone(self):
        return normalise_phone(self.cleaned_data.get("phone"))

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email") or ""
        phone = cleaned.get("phone") or ""
        if email or phone:
            self.duplicate_match = customers_matching_contact(
                email=email, phone=phone, exclude_pk=self.instance.pk
            ).first()
            if self.duplicate_match is not None and not cleaned.get("allow_duplicate"):
                raise forms.ValidationError(
                    _(
                        "%(name)s (%(code)s) is already registered with this e-mail or "
                        "phone number. Open that record, or tick the override below."
                    )
                    % {
                        "name": self.duplicate_match.full_name,
                        "code": self.duplicate_match.customer_code,
                    }
                )
        return cleaned

    def save(self, commit: bool = True):
        # Take ``tags`` out of ``cleaned_data`` so Django's generic ``_save_m2m``
        # leaves the through table alone — :meth:`save_tags` owns it and records
        # who applied each tag. With ``commit=False`` the caller must call
        # :meth:`save_tags` itself, after ``save_m2m()``.
        self._selected_tags = list(self.cleaned_data.pop("tags", []) or [])
        customer = super().save(commit=commit)
        if commit:
            self.save_tags(customer)
        return customer

    def save_tags(self, customer: Customer) -> None:
        """Sync the tag links (the M2M uses an explicit through model)."""
        wanted = {tag.pk for tag in self._selected_tags}
        current = set(
            CustomerTag.objects.filter(customer=customer).values_list("tag_id", flat=True)
        )
        CustomerTag.objects.filter(customer=customer, tag_id__in=current - wanted).delete()
        added_by = self.user if getattr(self.user, "is_authenticated", False) else None
        CustomerTag.objects.bulk_create(
            [
                CustomerTag(customer=customer, tag_id=tag_id, added_by=added_by)
                for tag_id in wanted - current
            ]
        )


class CustomerQuickCreateForm(TailwindFormMixin, forms.ModelForm):
    """Four-field form for the counter: enough to take a booking, no more."""

    class Meta:
        model = Customer
        fields = ("first_name", "last_name", "phone", "email", "source")
        widgets = {
            "phone": forms.TextInput(
                attrs={"inputmode": "tel", "placeholder": "+90 555 123 45 67"}
            ),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].widget.attrs["autofocus"] = True
        self.fields["email"].required = False
        self.fields["source"].initial = BookingSource.WALK_IN
        self.existing: Customer | None = None

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_phone(self):
        return normalise_phone(self.cleaned_data.get("phone"))

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email") or ""
        phone = cleaned.get("phone") or ""
        if not email and not phone:
            raise forms.ValidationError(_("Enter at least a phone number or an e-mail address."))
        self.existing = customers_matching_contact(email=email, phone=phone).first()
        if self.existing is not None:
            raise forms.ValidationError(
                _("%(name)s (%(code)s) is already on file — use that record.")
                % {"name": self.existing.full_name, "code": self.existing.customer_code}
            )
        return cleaned


class CustomerFilterForm(TailwindFormMixin, forms.Form):
    """Filters on the customer list. All optional, all bookmarkable."""

    STATUS_CHOICES = (
        ("", _("All statuses")),
        ("active", _("Active only")),
        ("inactive", _("Archived only")),
    )
    BOOKING_CHOICES = (
        ("", _("Any booking history")),
        ("yes", _("Has bookings")),
        ("no", _("Never booked")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": _("Name, code, e-mail or phone"), "autocomplete": "off"}
        ),
    )
    status = forms.ChoiceField(label=_("Status"), choices=STATUS_CHOICES, required=False)
    source = forms.ChoiceField(label=_("Source"), required=False)
    has_bookings = forms.ChoiceField(
        label=_("Bookings"), choices=BOOKING_CHOICES, required=False
    )
    tag = forms.ChoiceField(label=_("Tag"), required=False)
    minors_only = forms.BooleanField(label=_("Under 18 only"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].choices = [("", _("Any source"))] + list(BookingSource.choices)
        self.fields["tag"].choices = [("", _("Any tag"))] + [
            (slug, name) for slug, name in Tag.objects.values_list("slug", "name")
        ]


class CustomerNoteForm(TailwindFormMixin, forms.ModelForm):
    """Add a note to a customer's timeline."""

    class Meta:
        model = Note
        fields = ("body", "is_internal", "is_pinned")
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 3, "placeholder": _("What should the team know?")}
            )
        }
        labels = {"body": _("Note")}


class CustomerDocumentForm(TailwindFormMixin, forms.ModelForm):
    """Attach a waiver, ID copy, insurance certificate or medical note."""

    class Meta:
        model = Document
        fields = ("title", "category", "file", "expires_on", "is_confidential")
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_on"].help_text = _(
            "Leave empty for documents that never expire. A waiver past its "
            "expiry date no longer counts as signed."
        )


class CustomerMergeForm(TailwindFormMixin, forms.Form):
    """Choose which of two records survives a merge."""

    primary = forms.IntegerField(widget=forms.HiddenInput())
    duplicate = forms.IntegerField(widget=forms.HiddenInput())
    confirm = forms.BooleanField(
        label=_("I understand the duplicate will be archived and cannot be un-merged"),
        required=True,
    )

    def clean(self):
        cleaned = super().clean()
        primary_pk = cleaned.get("primary")
        duplicate_pk = cleaned.get("duplicate")
        if primary_pk and duplicate_pk and primary_pk == duplicate_pk:
            raise forms.ValidationError(_("Pick two different customers."))
        cleaned["primary_obj"] = Customer.objects.filter(pk=primary_pk).first()
        cleaned["duplicate_obj"] = Customer.objects.filter(pk=duplicate_pk).first()
        if cleaned["primary_obj"] is None or cleaned["duplicate_obj"] is None:
            raise forms.ValidationError(_("One of the selected customers no longer exists."))
        return cleaned
