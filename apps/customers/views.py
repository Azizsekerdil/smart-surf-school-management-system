"""Customer HTML views.

The detail screen is tabbed. Each tab has a real URL, so it works without
JavaScript: the link is an ordinary ``?tab=`` href that the server renders
inline, and HTMX simply swaps the panel instead of reloading the page.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import BookingSource
from apps.core.mixins import AuditedCreateMixin, AuditedUpdateMixin, HtmxPartialMixin

from . import selectors, services
from .forms import (
    CustomerDocumentForm,
    CustomerFilterForm,
    CustomerForm,
    CustomerMergeForm,
    CustomerNoteForm,
    CustomerQuickCreateForm,
)
from .models import Customer

#: Tabs on the detail screen, in display order.
DETAIL_TABS: tuple[tuple[str, object, str], ...] = (
    ("bookings", _("Bookings"), "calendar-days"),
    ("rentals", _("Rentals"), "arrow-left-right"),
    ("payments", _("Payments"), "wallet"),
    ("documents", _("Documents"), "file-text"),
    ("notes", _("Notes"), "message-square"),
)
TAB_KEYS = tuple(key for key, _label, _icon in DETAIL_TABS)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
class CustomerListView(CapabilityRequiredMixin, HtmxPartialMixin, ListView):
    capability = "customers.view"
    model = Customer
    template_name = "customers/customer_list.html"
    partial_template_name = "customers/partials/customer_table.html"
    context_object_name = "customers"
    paginate_by = 25

    def get_filter_form(self) -> CustomerFilterForm:
        if not hasattr(self, "_filter_form"):
            self._filter_form = CustomerFilterForm(self.request.GET or None)
            self._filter_form.is_valid()  # populates cleaned_data; all fields optional
        return self._filter_form

    def get_queryset(self):
        form = self.get_filter_form()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        return selectors.customer_list(
            search=data.get("q", ""),
            is_active=data.get("status", ""),
            source=data.get("source", ""),
            has_bookings=data.get("has_bookings", ""),
            tag=data.get("tag", ""),
            minors_only=bool(data.get("minors_only")),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        context["search_term"] = self.request.GET.get("q", "")
        context["total_count"] = Customer.objects.count()
        context["active_count"] = Customer.objects.active().count()
        return context


# ---------------------------------------------------------------------------
# Detail + tabs
# ---------------------------------------------------------------------------
def _tab_context(customer, tab: str) -> dict:
    """Build the data for one detail tab."""
    if tab == "bookings":
        rows = selectors.customer_bookings(customer)
        return {"tab": tab, "rows": rows, "customer": customer}
    if tab == "rentals":
        return {"tab": tab, "rows": selectors.customer_rentals(customer), "customer": customer}
    if tab == "payments":
        return {
            "tab": tab,
            "rows": selectors.customer_payments(customer),
            "invoices": selectors.customer_invoices(customer),
            "open_balance": selectors.open_balance(customer),
            "customer": customer,
        }
    if tab == "documents":
        return {
            "tab": tab,
            "rows": selectors.customer_documents(customer),
            "document_form": CustomerDocumentForm(),
            "customer": customer,
        }
    if tab == "notes":
        return {
            "tab": tab,
            "rows": selectors.customer_notes(customer),
            "note_form": CustomerNoteForm(),
            "customer": customer,
        }
    raise Http404(_("Unknown tab."))


class CustomerDetailView(CapabilityRequiredMixin, DetailView):
    capability = "customers.view"
    model = Customer
    template_name = "customers/customer_detail.html"
    context_object_name = "customer"

    def get_queryset(self):
        return Customer.objects.select_related("user").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = self.object
        tab = self.request.GET.get("tab", TAB_KEYS[0])
        if tab not in TAB_KEYS:
            tab = TAB_KEYS[0]
        context["tabs"] = [
            {"key": key, "label": label, "icon": icon} for key, label, icon in DETAIL_TABS
        ]
        context["active_tab"] = tab
        context["tab_context"] = _tab_context(customer, tab)
        context["has_valid_waiver"] = customer.has_valid_waiver()
        context["waiver"] = customer.waiver_document()
        context["student_profile"] = getattr(customer, "student_profile", None)
        context["duplicate_candidates"] = list(
            selectors.customers_matching_contact(
                email=customer.email, phone=customer.phone, exclude_pk=customer.pk
            )[:5]
        )
        return context


class CustomerTabView(CapabilityRequiredMixin, View):
    """HTMX endpoint returning one detail tab. Also renders standalone."""

    capability = "customers.view"

    def get(self, request, pk: int, tab: str):
        if tab not in TAB_KEYS:
            raise Http404(_("Unknown tab."))
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        context = _tab_context(customer, tab)
        return render(request, f"customers/partials/tab_{tab}.html", context)


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
class CustomerCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "customers.add"
    model = Customer
    form_class = CustomerForm
    template_name = "customers/customer_form.html"
    success_message = _("Customer created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New customer")
        context["submit_label"] = _("Create customer")
        return context

    def get_success_url(self):
        return reverse("customers:detail", kwargs={"pk": self.object.pk})


class CustomerUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "customers.change"
    model = Customer
    form_class = CustomerForm
    template_name = "customers/customer_form.html"
    context_object_name = "customer"
    success_message = _("Customer updated.")

    def get_queryset(self):
        return Customer.objects.all()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit customer")
        context["submit_label"] = _("Save changes")
        return context

    def get_success_url(self):
        return reverse("customers:detail", kwargs={"pk": self.object.pk})


# ---------------------------------------------------------------------------
# HTMX quick create (used by the booking and rental screens)
# ---------------------------------------------------------------------------
class CustomerQuickCreateView(CapabilityRequiredMixin, View):
    """Modal that turns a walk-in into a bookable customer in four fields.

    On success the response carries an ``HX-Trigger`` header::

        {"customerCreated": {"id": 12, "code": "CUS00012", "name": "...",
                             "phone": "...", "url": "/customers/12/"}}

    so the calling screen can select the new customer without a page reload.
    """

    capability = "customers.add"
    template_name = "customers/partials/quick_create_modal.html"

    def get(self, request):
        form = CustomerQuickCreateForm(user=request.user)
        return render(request, self.template_name, {"form": form, "target": self._target(request)})

    def post(self, request):
        form = CustomerQuickCreateForm(request.POST, user=request.user)
        if not form.is_valid():
            response = render(
                request,
                self.template_name,
                {"form": form, "target": self._target(request), "existing": form.existing},
                status=400,
            )
            return response

        customer = services.create_customer(
            first_name=form.cleaned_data["first_name"],
            last_name=form.cleaned_data["last_name"],
            email=form.cleaned_data["email"],
            phone=form.cleaned_data["phone"],
            source=form.cleaned_data["source"] or BookingSource.WALK_IN,
            actor=request.user,
            request=request,
        )
        payload = {
            "customerCreated": {
                "id": customer.pk,
                "code": customer.customer_code,
                "name": customer.full_name,
                "phone": customer.phone,
                "email": customer.email,
                "url": reverse("customers:detail", kwargs={"pk": customer.pk}),
                "target": self._target(request),
            }
        }
        response = render(
            request,
            "customers/partials/quick_create_done.html",
            {"customer": customer, "target": self._target(request)},
        )
        response["HX-Trigger"] = json.dumps(payload)
        return response

    @staticmethod
    def _target(request) -> str:
        """Optional DOM id of the field the caller wants filled in."""
        return (request.GET.get("target") or request.POST.get("target") or "").strip()[:64]


class CustomerSearchView(CapabilityRequiredMixin, View):
    """Type-ahead used by booking/rental screens: returns a small result list."""

    capability = "customers.view"

    def get(self, request):
        term = (request.GET.get("q") or "").strip()
        results = []
        if len(term) >= 2:
            results = list(Customer.objects.active().search(term)[:10])
        return render(
            request,
            "customers/partials/search_results.html",
            {"results": results, "term": term, "target": (request.GET.get("target") or "")[:64]},
        )


# ---------------------------------------------------------------------------
# Notes & documents
# ---------------------------------------------------------------------------
class CustomerNoteCreateView(CapabilityRequiredMixin, View):
    capability = "customers.change"

    def post(self, request, pk: int):
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        form = CustomerNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.content_type = ContentType.objects.get_for_model(Customer)
            note.object_id = customer.pk
            note.created_by = request.user
            note.updated_by = request.user
            note.save()
            record_audit(
                request,
                action=AuditAction.CREATE,
                instance=note,
                description=_("Note added to customer %(code)s")
                % {"code": customer.customer_code},
            )
            messages.success(request, _("Note added."))
        else:
            messages.error(request, _("The note could not be saved."))

        context = _tab_context(customer, "notes")
        if getattr(request, "htmx", False):
            return render(request, "customers/partials/tab_notes.html", context)
        return redirect(f"{reverse('customers:detail', kwargs={'pk': customer.pk})}?tab=notes")


class CustomerDocumentCreateView(CapabilityRequiredMixin, View):
    capability = "customers.change"

    def post(self, request, pk: int):
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        form = CustomerDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.content_type = ContentType.objects.get_for_model(Customer)
            document.object_id = customer.pk
            document.content_type_hint = getattr(
                request.FILES.get("file"), "content_type", ""
            )[:100]
            document.created_by = request.user
            document.updated_by = request.user
            document.save()
            record_audit(
                request,
                action=AuditAction.CREATE,
                instance=document,
                description=_("Document “%(title)s” attached to customer %(code)s")
                % {"title": document.title, "code": customer.customer_code},
            )
            messages.success(request, _("Document uploaded."))
        else:
            messages.error(
                request,
                _("The document could not be uploaded: %(errors)s")
                % {"errors": form.errors.as_text()},
            )

        context = _tab_context(customer, "documents")
        if getattr(request, "htmx", False):
            return render(request, "customers/partials/tab_documents.html", context)
        return redirect(f"{reverse('customers:detail', kwargs={'pk': customer.pk})}?tab=documents")


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------
class CustomerToggleActiveView(CapabilityRequiredMixin, View):
    """Archive or restore a customer. POST only — it changes state."""

    capability = "customers.change"

    def post(self, request, pk: int):
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        try:
            if customer.is_active:
                services.deactivate_customer(
                    customer,
                    reason=(request.POST.get("reason") or "").strip()[:200],
                    actor=request.user,
                    request=request,
                )
                messages.success(request, _("Customer archived."))
            else:
                services.reactivate_customer(customer, actor=request.user, request=request)
                messages.success(request, _("Customer reactivated."))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("customers:detail", pk=customer.pk)


class CustomerConsentView(CapabilityRequiredMixin, View):
    """Record a marketing opt-in / opt-out."""

    capability = "customers.change"

    def post(self, request, pk: int):
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        granted = request.POST.get("granted") == "1"
        services.set_marketing_consent(customer, granted, actor=request.user, request=request)
        messages.success(
            request,
            _("Marketing consent recorded.") if granted else _("Marketing consent withdrawn."),
        )
        return redirect("customers:detail", pk=customer.pk)


class CustomerRecalculateView(CapabilityRequiredMixin, View):
    """Rebuild the money and visit roll-ups from the owning modules."""

    capability = "customers.change"

    def post(self, request, pk: int):
        customer = get_object_or_404(Customer.objects.all(), pk=pk)
        services.recalculate_lifetime_value(customer)
        messages.success(request, _("Totals recalculated."))
        return redirect("customers:detail", pk=customer.pk)


# ---------------------------------------------------------------------------
# Duplicates & merge
# ---------------------------------------------------------------------------
class CustomerDuplicateListView(CapabilityRequiredMixin, TemplateView):
    capability = "customers.manage"
    template_name = "customers/duplicate_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["groups"] = services.find_duplicates()
        return context


class CustomerMergeView(CapabilityRequiredMixin, TemplateView):
    """Confirm-then-merge. The survivor keeps its code; the other is archived."""

    capability = "customers.manage"
    template_name = "customers/customer_merge.html"

    def _pair(self):
        primary = get_object_or_404(Customer.objects.all(), pk=self.kwargs["pk"])
        duplicate = get_object_or_404(Customer.objects.all(), pk=self.kwargs["duplicate_pk"])
        return primary, duplicate

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        primary, duplicate = self._pair()
        context["primary"] = primary
        context["duplicate"] = duplicate
        context["pair"] = [primary, duplicate]
        context.setdefault(
            "form",
            CustomerMergeForm(initial={"primary": primary.pk, "duplicate": duplicate.pk}),
        )
        return context

    def post(self, request, *args, **kwargs):
        primary, duplicate = self._pair()
        form = CustomerMergeForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    services.merge_customers(
                        form.cleaned_data["primary_obj"],
                        form.cleaned_data["duplicate_obj"],
                        actor=request.user,
                        request=request,
                    )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return self.render_to_response(self.get_context_data(form=form))
            messages.success(
                request,
                _("%(dup)s was merged into %(primary)s.")
                % {
                    "dup": duplicate.customer_code,
                    "primary": primary.customer_code,
                },
            )
            return redirect("customers:detail", pk=form.cleaned_data["primary_obj"].pk)
        return self.render_to_response(self.get_context_data(form=form))
