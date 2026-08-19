"""AI Development Terminal screens."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin, require_capability

from . import agent, security, services
from .forms import AgentRequestForm, ApprovalForm, CommandForm
from .models import CodeChangeProposal, TerminalCommand, TerminalSession

logger = logging.getLogger("apps.ai_terminal")


class ConsoleView(CapabilityRequiredMixin, TemplateView):
    """The terminal itself: history, command entry and the agent panel."""

    capability = "ai_terminal.view"
    template_name = "ai_terminal/console.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session_id = self.request.GET.get("s")
        session = services.get_or_create_session(
            self.request.user, int(session_id) if str(session_id).isdigit() else None
        )

        context["session"] = session
        context["commands"] = session.commands.select_related(
            "requested_by", "approved_by"
        ).order_by("created_at")
        context["proposals"] = session.proposals.order_by("-created_at")[:20]
        context["sessions"] = TerminalSession.objects.filter(user=self.request.user)[:15]
        context["command_form"] = CommandForm()
        context["agent_form"] = AgentRequestForm()
        context["policy"] = security.describe_policy()
        context["pending_count"] = session.commands.filter(
            status=TerminalCommand.Status.AWAITING_APPROVAL
        ).count()
        context["can_approve"] = self.request.user.has_capability("ai_terminal.approve")
        context["can_apply"] = self.request.user.has_capability("ai_terminal.apply_patch")
        context["examples"] = [
            "git status",
            "git diff",
            "python manage.py check",
            "python manage.py test apps.bookings",
            "pytest apps/rentals -q",
            "ruff check apps",
            "pip list",
        ]
        return context


@require_POST
def run_command(request):
    """Validate (and, when safe, run) a typed command. HTMX endpoint."""
    require_capability(request.user, "ai_terminal.execute")

    form = CommandForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "ai_terminal/partials/command_row.html",
            {"error": _("Enter a command.")},
            status=400,
        )

    session_id = request.POST.get("session_id")
    session = services.get_or_create_session(
        request.user, int(session_id) if str(session_id).isdigit() else None
    )

    try:
        entry = services.propose_and_maybe_run(
            session,
            form.cleaned_data["command"],
            user=request.user,
            rationale=form.cleaned_data.get("rationale", ""),
        )
    except (PermissionDenied, ValidationError) as exc:
        return render(
            request,
            "ai_terminal/partials/command_row.html",
            {"error": "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)},
            status=403 if isinstance(exc, PermissionDenied) else 400,
        )

    return render(
        request,
        "ai_terminal/partials/command_row.html",
        {
            "command": entry,
            "can_approve": request.user.has_capability("ai_terminal.approve"),
        },
    )


@require_POST
def approve_command(request, pk: int):
    entry = get_object_or_404(TerminalCommand, pk=pk)
    form = ApprovalForm(request.POST)
    form.is_valid()

    try:
        services.approve_command(
            entry,
            user=request.user,
            note=form.cleaned_data.get("note", ""),
            edited=form.cleaned_data.get("edited_command", ""),
        )
        entry = services.execute_command(entry, user=request.user)
    except (PermissionDenied, ValidationError) as exc:
        entry.refresh_from_db()
        return render(
            request,
            "ai_terminal/partials/command_row.html",
            {
                "command": entry,
                "error": "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
                "can_approve": request.user.has_capability("ai_terminal.approve"),
            },
            status=400,
        )

    return render(
        request,
        "ai_terminal/partials/command_row.html",
        {"command": entry, "can_approve": True},
    )


@require_POST
def reject_command(request, pk: int):
    entry = get_object_or_404(TerminalCommand, pk=pk)
    form = ApprovalForm(request.POST)
    form.is_valid()
    try:
        entry = services.reject_command(
            entry, user=request.user, note=form.cleaned_data.get("note", "")
        )
    except PermissionDenied as exc:
        return render(
            request,
            "ai_terminal/partials/command_row.html",
            {"command": entry, "error": str(exc)},
            status=403,
        )
    return render(
        request, "ai_terminal/partials/command_row.html", {"command": entry, "can_approve": True}
    )


@require_POST
def explain_command(request):
    require_capability(request.user, "ai_terminal.view")
    command = request.POST.get("command", "").strip()
    explanation = agent.explain_command(command, user=request.user) if command else ""
    result = security.validate_command(command) if command else None
    return render(
        request,
        "ai_terminal/partials/explanation.html",
        {"command": command, "explanation": explanation, "policy": result},
    )


@require_POST
def run_agent(request):
    """Ask the development agent for a plan and code proposals."""
    require_capability(request.user, "ai_terminal.execute")

    form = AgentRequestForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "ai_terminal/partials/agent_report.html",
            {"report": {"ok": False, "error": _("Describe what the agent should do.")}},
            status=400,
        )

    session_id = request.POST.get("session_id")
    session = services.get_or_create_session(
        request.user, int(session_id) if str(session_id).isdigit() else None
    )
    if not session.goal:
        TerminalSession.objects.filter(pk=session.pk).update(
            goal=form.cleaned_data["request_text"][:2000],
            title=form.cleaned_data["request_text"][:80],
        )

    report = agent.run_agent(
        session,
        form.cleaned_data["request_text"],
        user=request.user,
        context_files=form.cleaned_context_files(),
    )
    return render(
        request,
        "ai_terminal/partials/agent_report.html",
        {
            "report": report,
            "session": session,
            "can_apply": request.user.has_capability("ai_terminal.apply_patch"),
        },
    )


class ProposalListView(CapabilityRequiredMixin, ListView):
    capability = "ai_terminal.view"
    model = CodeChangeProposal
    template_name = "ai_terminal/proposal_list.html"
    context_object_name = "proposals"
    paginate_by = 25

    def get_queryset(self):
        return CodeChangeProposal.objects.select_related("session", "approved_by").order_by(
            "-created_at"
        )


class ProposalDetailView(CapabilityRequiredMixin, DetailView):
    capability = "ai_terminal.view"
    model = CodeChangeProposal
    template_name = "ai_terminal/proposal_detail.html"
    context_object_name = "proposal"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_approve"] = self.request.user.has_capability("ai_terminal.approve")
        context["can_apply"] = self.request.user.has_capability("ai_terminal.apply_patch")
        context["diff_lines"] = _annotate_diff(self.object.unified_diff)
        return context


def _annotate_diff(diff: str) -> list[dict]:
    """Tag each diff line so the template can colour it without regex in HTML."""
    annotated = []
    for line in (diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "remove"
        else:
            kind = "context"
        annotated.append({"kind": kind, "text": line})
    return annotated


@require_POST
def approve_proposal(request, pk: int):
    require_capability(request.user, "ai_terminal.approve")
    proposal = get_object_or_404(CodeChangeProposal, pk=pk)

    if proposal.status != CodeChangeProposal.Status.AWAITING_APPROVAL:
        messages.error(request, _("This proposal is not awaiting approval."))
        return redirect("ai_terminal:proposal_detail", pk=pk)

    from django.utils import timezone

    proposal.status = CodeChangeProposal.Status.APPROVED
    proposal.approved_by = request.user
    proposal.decided_at = timezone.now()
    proposal.decision_note = request.POST.get("note", "")[:500]
    proposal.save(update_fields=["status", "approved_by", "decided_at", "decision_note", "updated_at"])

    # A checkpoint branch makes the change trivially abandonable.
    if request.POST.get("create_checkpoint"):
        ok, detail = services.create_checkpoint_branch(
            proposal.session, user=request.user, label=proposal.title
        )
        if ok:
            CodeChangeProposal.objects.filter(pk=proposal.pk).update(checkpoint_branch=detail)
            messages.info(request, _("Checkpoint branch created: %(b)s") % {"b": detail})
        else:
            messages.warning(request, _("No checkpoint branch: %(d)s") % {"d": detail})

    messages.success(request, _("Proposal approved. You can now apply it."))
    return redirect("ai_terminal:proposal_detail", pk=pk)


@require_POST
def reject_proposal(request, pk: int):
    require_capability(request.user, "ai_terminal.approve")
    proposal = get_object_or_404(CodeChangeProposal, pk=pk)

    from django.utils import timezone

    proposal.status = CodeChangeProposal.Status.REJECTED
    proposal.approved_by = request.user
    proposal.decided_at = timezone.now()
    proposal.decision_note = request.POST.get("note", "")[:500]
    proposal.save(update_fields=["status", "approved_by", "decided_at", "decision_note", "updated_at"])
    messages.info(request, _("Proposal rejected."))
    return redirect("ai_terminal:proposal_list")


@require_POST
def apply_proposal(request, pk: int):
    proposal = get_object_or_404(CodeChangeProposal, pk=pk)
    try:
        services.apply_proposal(proposal, user=request.user)
        messages.success(
            request, _("Change applied to %(path)s.") % {"path": proposal.file_path}
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(
            request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        )
    return redirect("ai_terminal:proposal_detail", pk=pk)


@require_POST
def revert_proposal(request, pk: int):
    proposal = get_object_or_404(CodeChangeProposal, pk=pk)
    try:
        services.revert_proposal(proposal, user=request.user)
        messages.success(request, _("Change reverted."))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(
            request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        )
    return redirect("ai_terminal:proposal_detail", pk=pk)


class PolicyView(CapabilityRequiredMixin, TemplateView):
    """Human-readable view of exactly what the terminal will and will not run."""

    capability = "ai_terminal.view"
    template_name = "ai_terminal/policy.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["policy"] = security.describe_policy()
        context["rules"] = security.ALLOWED_COMMANDS
        return context
