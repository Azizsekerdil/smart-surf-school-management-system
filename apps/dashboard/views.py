"""Dashboard and global-search views.

Three routes:

``dashboard:home``    the operations screen, composed per role.
``dashboard:search``  the topbar's global search, capability-scoped.
``dashboard:tiles``   the HTMX fragment the tile grid refreshes itself with.

Every route requires ``dashboard.view``, which every authenticated role holds —
the *content* is then narrowed by the capabilities the user actually has, in
:mod:`apps.dashboard.services`.
"""

from __future__ import annotations

from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin

from . import selectors, services

#: Variant -> the template that lays it out.
VARIANT_TEMPLATES = {
    "staff": "dashboard/home.html",
    "instructor": "dashboard/home.html",
    "customer": "dashboard/home_customer.html",
}


class DashboardHomeView(CapabilityRequiredMixin, TemplateView):
    """The main dashboard.

    The role decides both the context builder and the template, so a customer's
    self-service page cannot accidentally inherit a staff panel from a shared
    layout.
    """

    capability = "dashboard.view"
    template_name = "dashboard/home.html"

    #: Set while the context is built; read afterwards by ``get_template_names``.
    variant = "staff"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(services.build_dashboard_context(self.request.user))
        # Remembered rather than recomputed: the builder is the expensive part
        # and Django always calls get_context_data() before it picks a template.
        self.variant = context.get("dashboard_variant", "staff")
        context["tiles_url"] = reverse("dashboard:tiles")
        context["refreshed_at"] = timezone.localtime()
        return context

    def get_template_names(self):
        return [VARIANT_TEMPLATES.get(self.variant, self.template_name)]


class DashboardTilesView(CapabilityRequiredMixin, TemplateView):
    """HTMX endpoint: re-render just the stat tiles.

    Lets the beach desk leave the dashboard open all day and still see the
    current numbers without a full page load — and without re-running the
    schedule, panel and chart queries the tiles do not need.
    """

    capability = "dashboard.view"
    template_name = "dashboard/partials/tiles.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()

        if getattr(user, "is_external", False):
            # External accounts have their own small tile set, built from their
            # own records only.
            context.update(services.build_customer_dashboard(user, today))
        else:
            context["tiles"] = services.build_tiles(user, today)
            context["today"] = today
        context["tiles_url"] = reverse("dashboard:tiles")
        context["refreshed_at"] = timezone.localtime()
        return context


class GlobalSearchView(CapabilityRequiredMixin, TemplateView):
    """Search across every module the signed-in user is allowed to view.

    An exact asset, booking, customer, student, rental or lesson code goes
    straight to that record — the desk types a code off a board or a receipt far
    more often than it browses results.
    """

    capability = "dashboard.view"
    template_name = "dashboard/search.html"

    def get(self, request, *args, **kwargs):
        term = (request.GET.get("q") or "").strip()
        if term:
            target = selectors.direct_hit_url(request.user, term)
            if target:
                return redirect(target)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = (self.request.GET.get("q") or "").strip()
        context["results"] = services.global_search(self.request.user, term)
        context["search_term"] = term
        context["min_length"] = selectors.MIN_SEARCH_LENGTH
        return context
