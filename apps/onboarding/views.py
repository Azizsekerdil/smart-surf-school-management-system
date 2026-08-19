"""The first-run setup wizard.

One view drives every step, so progress, navigation and permission checks stay
consistent. Each step either edits the shared ``OnboardingState`` row through a
small ModelForm or is purely informational; :data:`STEP_FORMS` says which.

Nothing here is mandatory — the wizard can be skipped at any point, and Finish
is the only action that writes records elsewhere in the system.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin, require_capability

from . import services
from .forms import (
    AISetupForm,
    BackupSetupForm,
    BusinessInfoForm,
    CurrencyForm,
    LanguageForm,
    LocationForm,
)
from .models import OnboardingState

#: Step slug -> form class. Slugs without an entry are informational.
STEP_FORMS = {
    "business": BusinessInfoForm,
    "language": LanguageForm,
    "currency": CurrencyForm,
    "location": LocationForm,
    "ai": AISetupForm,
    "backup": BackupSetupForm,
}


class OnboardingWizardView(CapabilityRequiredMixin, TemplateView):
    """Renders one step, and saves it on POST."""

    capability = "onboarding.view"
    template_name = "onboarding/wizard.html"

    @property
    def step(self) -> str:
        requested = self.kwargs.get("step") or ""
        valid = [slug for slug, _label in OnboardingState.STEPS]
        return requested if requested in valid else valid[0]

    def get_form(self, state, data=None):
        form_class = STEP_FORMS.get(self.step)
        if form_class is None:
            return None
        return form_class(data=data, instance=state)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        state = services.prefill_state(OnboardingState.get_state())
        step = self.step

        context["state"] = state
        context["step"] = step
        context["step_number"] = OnboardingState.step_number(step)
        context["step_total"] = len(OnboardingState.STEPS)
        context["step_label"] = dict(OnboardingState.STEPS).get(step, step)
        context["steps"] = [
            {
                "key": slug,
                "label": label,
                "number": index,
                "is_current": slug == step,
                "is_done": state.has_answered(slug),
                "url": reverse("onboarding:step", kwargs={"step": slug}),
            }
            for index, (slug, label) in enumerate(OnboardingState.STEPS, start=1)
        ]
        context["percent"] = state.percent_complete
        context["previous_step"] = state.previous_step(step)
        context["next_step"] = state.next_step(step)
        context.setdefault("form", self.get_form(state))

        # Step-specific context, all of it defensive: a disabled module hides a
        # button rather than breaking the page.
        if step == "staff":
            context["staff"] = services.staff_overview()
            context["user_create_url"] = services.resolve_optional_url("accounts:user_create")
            context["role_matrix_url"] = services.resolve_optional_url("accounts:role_matrix")
        elif step == "ai":
            context["ai_control_url"] = services.resolve_optional_url("ai:control_center")
        elif step == "backup":
            context["backup_url"] = services.resolve_optional_url("backups:list")
        elif step == "location":
            context["existing_spot"] = services.existing_primary_spot()
            context["spot_count"] = services.spot_count()
            context["locations_url"] = services.resolve_optional_url("locations:list")
        elif step == "finish":
            context["summary"] = services.summary_rows(state)

        return context

    def post(self, request, *args, **kwargs):
        require_capability(request.user, "onboarding.change")
        state = OnboardingState.get_state()
        step = self.step

        form = self.get_form(state, data=request.POST)
        if form is not None:
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(form=form))
            answered = form.has_any_answer()
            form.save()
            state.refresh_from_db()
        else:
            # An informational step counts as answered once it is passed through.
            answered = True

        services.record_step(
            state,
            slug=step,
            number=OnboardingState.step_number(step),
            answered=answered,
        )

        next_step = state.next_step(step)
        return redirect("onboarding:step", step=next_step or "finish")


@require_POST
def finish(request):
    """Apply the wizard's answers."""
    require_capability(request.user, "onboarding.change")
    state = OnboardingState.get_state()

    result = services.complete_onboarding(state, request=request, user=request.user)

    spot = result.get("spot")
    if result.get("spot_created") and spot is not None:
        messages.success(request, _("Surf spot created: %(name)s") % {"name": spot})
    elif spot is not None:
        messages.info(request, _("Using the existing surf spot: %(name)s") % {"name": spot})

    written = result.get("settings_written") or []
    if written:
        messages.success(request, _("%(count)s setting(s) saved.") % {"count": len(written)})

    messages.success(request, _("Setup complete. Welcome aboard."))
    return redirect("dashboard:home")


@require_POST
def skip(request):
    """Stop asking, without writing anything."""
    require_capability(request.user, "onboarding.view")
    services.skip_onboarding(OnboardingState.get_state(), request=request, user=request.user)
    messages.info(request, _("Setup skipped. You can reopen it any time from Settings."))
    return redirect("dashboard:home")


@require_POST
def reopen(request):
    """Reopen the wizard, keeping the previous answers."""
    require_capability(request.user, "onboarding.change")
    state = services.restart_onboarding(
        OnboardingState.get_state(), request=request, user=request.user
    )
    return redirect("onboarding:step", step=state.current_slug)


@require_POST
def dismiss_banner(request):
    """Hide the dashboard banner for the rest of this session."""
    services.dismiss_banner(request)
    return render(request, "onboarding/partials/banner.html", {"show": False})


def banner(request):
    """HTMX fragment: the dashboard's prompt to finish setting up."""
    show = services.should_show_banner(request)
    return render(
        request,
        "onboarding/partials/banner.html",
        {"show": show, "state": OnboardingState.get_state() if show else None},
    )
