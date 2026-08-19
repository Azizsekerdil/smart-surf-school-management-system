"""Notification screens.

Everything here is scoped to ``request.user``. There is no view — and no
capability — that shows one person another person's notifications; the only
privileged screen is the broadcast composer, which writes but never reads.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from apps.accounts.constants import Role
from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.mixins import AuditedUpdateMixin, HtmxPartialMixin

from . import selectors, services
from .context_processors import invalidate_unread_cache
from .forms import BroadcastForm, NotificationPreferenceForm
from .models import (
    Notification,
    NotificationCategory,
    NotificationLevel,
    NotificationPreference,
)


class NotificationListView(CapabilityRequiredMixin, HtmxPartialMixin, ListView):
    """The user's own inbox, filterable by read state, category and level."""

    capability = "notifications.view"
    model = Notification
    template_name = "notifications/notification_list.html"
    partial_template_name = "notifications/partials/notification_list.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        return selectors.filtered_notifications(
            self.request.user,
            unread_only=self.request.GET.get("unread") == "1",
            category=self.request.GET.get("category", "").strip(),
            level=self.request.GET.get("level", "").strip(),
            search=self.request.GET.get("q", ""),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = NotificationCategory.choices
        context["levels"] = NotificationLevel.choices
        context["unread_total"] = selectors.unread_count(self.request.user)
        context["unread_by_category"] = selectors.unread_counts_by_category(self.request.user)
        context["filters"] = {
            "unread": self.request.GET.get("unread", ""),
            "category": self.request.GET.get("category", ""),
            "level": self.request.GET.get("level", ""),
            "q": self.request.GET.get("q", ""),
        }
        return context


class NotificationDropdownView(CapabilityRequiredMixin, TemplateView):
    """The bell menu fragment: the ten most recent notifications.

    Served as a standalone partial so the topbar can load it lazily
    (``hx-get`` on first open) instead of paying for it on every page render.
    """

    capability = "notifications.view"
    template_name = "notifications/partials/notification_dropdown.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["notifications"] = selectors.recent_for(
            self.request.user, selectors.DROPDOWN_LIMIT
        )
        context["unread_total"] = selectors.unread_count(self.request.user)
        return context


# ---------------------------------------------------------------------------
# State changes — POST only, CSRF protected
# ---------------------------------------------------------------------------
class _OwnNotificationMixin(CapabilityRequiredMixin):
    """Loads a notification and refuses anything the user does not own."""

    capability = "notifications.view"
    http_method_names = ["post"]

    def get_notification(self) -> Notification:
        return get_object_or_404(
            Notification.objects.filter(recipient=self.request.user), pk=self.kwargs["pk"]
        )

    def row_response(self, notification: Notification) -> HttpResponse:
        """Swap the single row back in and refresh the counter out of band."""
        invalidate_unread_cache(self.request)
        return render(
            self.request,
            "notifications/partials/notification_row_swap.html",
            {
                "notification": notification,
                "unread_total": selectors.unread_count(self.request.user),
            },
        )

    def fallback_redirect(self) -> HttpResponseRedirect:
        """Where a non-HTMX POST lands: back on the list it came from."""
        referer = self.request.META.get("HTTP_REFERER", "")
        if referer and url_has_allowed_host_and_scheme(
            referer, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return redirect(referer)
        return redirect("notifications:list")


class NotificationMarkReadView(_OwnNotificationMixin, View):
    def post(self, request, *args, **kwargs):
        notification = self.get_notification()
        services.mark_read(notification, request.user)
        if request.htmx:
            return self.row_response(notification)
        return self.fallback_redirect()


class NotificationMarkUnreadView(_OwnNotificationMixin, View):
    def post(self, request, *args, **kwargs):
        notification = self.get_notification()
        services.mark_unread(notification, request.user)
        if request.htmx:
            return self.row_response(notification)
        return self.fallback_redirect()


class NotificationMarkAllReadView(CapabilityRequiredMixin, View):
    """Clear the whole inbox, honouring the category filter that is on screen."""

    capability = "notifications.view"
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        category = request.POST.get("category", "").strip()
        count = services.mark_all_read(
            request.user, category=category if category in NotificationCategory.values else ""
        )
        invalidate_unread_cache(request)
        messages.success(
            request,
            _("%(count)s notification(s) marked as read.") % {"count": count},
        )
        if request.htmx:
            # A full refresh also updates the bell badge in the topbar, which
            # this response cannot reach.
            return HttpResponse(status=204, headers={"HX-Refresh": "true"})

        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("notifications:list")


class NotificationOpenView(CapabilityRequiredMixin, View):
    """Mark one notification read and follow its link.

    A POST rather than a link because it changes state; the target is
    re-validated here so a stored path can never redirect off-site.
    """

    capability = "notifications.view"
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        notification = get_object_or_404(
            Notification.objects.filter(recipient=request.user), pk=kwargs["pk"]
        )
        services.mark_read(notification, request.user)
        invalidate_unread_cache(request)

        target = notification.link_url
        if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(target)
        return redirect("notifications:list")


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
class NotificationPreferenceUpdateView(
    CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView
):
    """Every user edits their own delivery rules — there is no other row."""

    capability = "notifications.view"
    model = NotificationPreference
    form_class = NotificationPreferenceForm
    template_name = "notifications/preference_form.html"
    context_object_name = "preference"
    success_url = reverse_lazy("notifications:preferences")
    success_message = gettext_lazy("Notification preferences saved.")

    def get_object(self, queryset=None) -> NotificationPreference:
        return NotificationPreference.for_user(self.request.user, create=True)


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------
class NotificationBroadcastView(CapabilityRequiredMixin, FormView):
    """Send one message to every active member of the chosen roles."""

    capability = "notifications.add"
    template_name = "notifications/broadcast_form.html"
    form_class = BroadcastForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["role_labels"] = dict(Role.choices)
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        delivered = services.notify_role(
            data["roles"],
            data["category"],
            data["title"],
            data["body"],
            level=data["level"],
            link_url=data["link_url"],
            exclude_user=self.request.user,
        )
        record_audit(
            self.request,
            action=AuditAction.SYSTEM,
            description=_("Broadcast “%(title)s” sent to %(count)s user(s)")
            % {"title": data["title"], "count": len(delivered)},
            changes={"roles": [None, ", ".join(data["roles"])]},
        )
        if delivered:
            messages.success(
                self.request,
                _("Message delivered to %(count)s user(s).") % {"count": len(delivered)},
            )
        else:
            messages.warning(
                self.request,
                _("Nobody received the message — the selected roles have no active users, "
                  "or every one of them has muted this category."),
            )
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return reverse("notifications:list")
