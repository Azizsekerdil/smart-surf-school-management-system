"""AI terminal business logic: propose → approve → execute → audit.

Every path through this module writes an audit entry, including refusals, so the
question "what did the AI try to do?" always has an answer.
"""

from __future__ import annotations

import difflib
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit

from . import executor, security
from .models import CodeChangeProposal, TerminalCommand, TerminalSession

logger = logging.getLogger("apps.ai_terminal")

#: File types the agent may read or edit. Binary and secret files are excluded.
EDITABLE_SUFFIXES = {
    ".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".po", ".cfg",
    ".ini", ".toml", ".yml", ".yaml", ".ps1",
}
MAX_FILE_BYTES = 512 * 1024


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def get_or_create_session(user, session_id: int | None = None, goal: str = "") -> TerminalSession:
    if session_id:
        session = TerminalSession.objects.filter(pk=session_id, user=user).first()
        if session is not None:
            return session
    return TerminalSession.objects.create(user=user, goal=goal, title=goal[:80])


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def propose_command(
    session: TerminalSession,
    command: str,
    *,
    user,
    origin: str = TerminalCommand.Origin.USER,
    rationale: str = "",
) -> TerminalCommand:
    """Validate *command* and record what the policy decided.

    Returns a persisted :class:`TerminalCommand`. Nothing is executed here —
    a safe command still has to go through :func:`execute_command`, so the
    proposal and the execution are always separately auditable.
    """
    if not user.has_capability("ai_terminal.execute"):
        raise PermissionDenied(_("You are not permitted to use the development terminal."))

    result = security.validate_command(command)

    status = {
        security.Risk.SAFE: TerminalCommand.Status.APPROVED,
        security.Risk.REQUIRES_APPROVAL: TerminalCommand.Status.AWAITING_APPROVAL,
        security.Risk.BLOCKED: TerminalCommand.Status.BLOCKED,
    }[result.risk]

    entry = TerminalCommand.objects.create(
        session=session,
        origin=origin,
        command=command,
        argv=result.argv,
        rationale=rationale,
        risk=result.risk.value,
        policy_rule=result.matched_rule[:100],
        policy_reason=result.reason[:500],
        status=status,
        requested_by=user,
    )

    record_audit(
        None,
        user=user,
        action=AuditAction.AI_COMMAND_PROPOSED,
        instance=entry,
        description=_("Terminal command proposed: %(cmd)s (%(risk)s)")
        % {"cmd": command[:200], "risk": result.risk.value},
        changes={"policy": [None, result.matched_rule], "reason": [None, result.reason]},
    )

    if result.risk == security.Risk.BLOCKED:
        logger.warning(
            "Blocked terminal command",
            extra={"command": command[:200], "rule": result.matched_rule, "user": user.username},
        )
    return entry


def approve_command(entry: TerminalCommand, *, user, note: str = "", edited: str = "") -> TerminalCommand:
    """Approve a pending command, optionally after editing it."""
    if not user.has_capability("ai_terminal.approve"):
        raise PermissionDenied(_("You are not permitted to approve terminal commands."))
    if entry.status != TerminalCommand.Status.AWAITING_APPROVAL:
        raise ValidationError(_("This command is not awaiting approval."))

    if edited and edited.strip() != entry.command.strip():
        # An edited command is re-validated from scratch — approval does not
        # grant a bypass of the policy.
        revalidated = security.validate_command(edited)
        if revalidated.risk == security.Risk.BLOCKED:
            entry.status = TerminalCommand.Status.BLOCKED
            entry.policy_reason = revalidated.reason[:500]
            entry.decided_at = timezone.now()
            entry.save(update_fields=["status", "policy_reason", "decided_at"])
            raise ValidationError(
                _("The edited command is blocked by policy: %(reason)s")
                % {"reason": revalidated.reason}
            )
        entry.edited_command = edited
        entry.argv = revalidated.argv
        entry.risk = revalidated.risk.value
        entry.policy_rule = revalidated.matched_rule[:100]

    entry.status = TerminalCommand.Status.APPROVED
    entry.approved_by = user
    entry.decided_at = timezone.now()
    entry.decision_note = note[:500]
    entry.save()

    record_audit(
        None,
        user=user,
        action=AuditAction.AI_COMMAND_APPROVED,
        instance=entry,
        description=_("Terminal command approved: %(cmd)s") % {"cmd": entry.effective_command[:200]},
    )
    return entry


def reject_command(entry: TerminalCommand, *, user, note: str = "") -> TerminalCommand:
    if not user.has_capability("ai_terminal.approve"):
        raise PermissionDenied(_("You are not permitted to decide on terminal commands."))

    entry.status = TerminalCommand.Status.REJECTED
    entry.approved_by = user
    entry.decided_at = timezone.now()
    entry.decision_note = note[:500]
    entry.save(update_fields=["status", "approved_by", "decided_at", "decision_note", "updated_at"])

    record_audit(
        None,
        user=user,
        action=AuditAction.AI_COMMAND_REJECTED,
        instance=entry,
        description=_("Terminal command rejected: %(cmd)s") % {"cmd": entry.command[:200]},
    )
    return entry


def execute_command(entry: TerminalCommand, *, user) -> TerminalCommand:
    """Run an approved command."""
    if not user.has_capability("ai_terminal.execute"):
        raise PermissionDenied(_("You are not permitted to run terminal commands."))
    if entry.status != TerminalCommand.Status.APPROVED:
        raise ValidationError(
            _("Only approved commands can run (this one is “%(status)s”).")
            % {"status": entry.get_status_display()}
        )

    # Re-validate immediately before execution: policy or configuration could
    # have changed between approval and now.
    revalidated = security.validate_command(entry.effective_command)
    if revalidated.risk == security.Risk.BLOCKED:
        entry.status = TerminalCommand.Status.BLOCKED
        entry.policy_reason = revalidated.reason[:500]
        entry.save(update_fields=["status", "policy_reason", "updated_at"])
        raise ValidationError(
            _("Blocked by policy at execution time: %(reason)s") % {"reason": revalidated.reason}
        )

    TerminalCommand.objects.filter(pk=entry.pk).update(status=TerminalCommand.Status.RUNNING)

    result = executor.execute(revalidated.argv)

    entry.exit_code = result.exit_code
    entry.stdout = result.stdout
    entry.stderr = result.stderr
    entry.duration_ms = result.duration_ms
    entry.output_truncated = result.truncated
    entry.executed_at = timezone.now()
    if result.timed_out:
        entry.status = TerminalCommand.Status.TIMED_OUT
    elif result.error:
        entry.status = TerminalCommand.Status.FAILED
        entry.stderr = f"{entry.stderr}\n{result.error}".strip()
    elif result.exit_code == 0:
        entry.status = TerminalCommand.Status.COMPLETED
    else:
        entry.status = TerminalCommand.Status.FAILED
    entry.save()

    record_audit(
        None,
        user=user,
        action=AuditAction.AI_COMMAND_EXECUTED,
        instance=entry,
        description=_("Terminal command executed: %(cmd)s → exit %(code)s in %(ms)sms")
        % {"cmd": entry.effective_command[:200], "code": result.exit_code, "ms": result.duration_ms},
    )
    return entry


def propose_and_maybe_run(
    session: TerminalSession, command: str, *, user, origin=TerminalCommand.Origin.USER, rationale: str = ""
) -> TerminalCommand:
    """Convenience path: safe commands run straight away, others wait."""
    entry = propose_command(session, command, user=user, origin=origin, rationale=rationale)
    if entry.status == TerminalCommand.Status.APPROVED:
        return execute_command(entry, user=user)
    return entry


# ---------------------------------------------------------------------------
# File access
# ---------------------------------------------------------------------------
def safe_read_file(relative_path: str) -> tuple[bool, str]:
    """Read a workspace file, or explain why not."""
    ok, why = security.is_within_workspace(relative_path)
    if not ok:
        return False, why

    path = (security.get_workspace() / relative_path).resolve()
    if not path.exists() or not path.is_file():
        return False, _("File not found: %(path)s") % {"path": relative_path}
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        return False, _("'%(suffix)s' files cannot be read here.") % {"suffix": path.suffix}
    if path.stat().st_size > MAX_FILE_BYTES:
        return False, _("File is larger than %(kb)s KB.") % {"kb": MAX_FILE_BYTES // 1024}

    try:
        return True, path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, _("Could not read the file: %(error)s") % {"error": type(exc).__name__}


def list_workspace_files(subdirectory: str = "", pattern: str = "*") -> list[str]:
    """List editable files under the workspace, relative and sorted."""
    ok, _why = security.is_within_workspace(subdirectory or ".")
    if not ok:
        return []

    workspace = security.get_workspace()
    root = (workspace / subdirectory).resolve() if subdirectory else workspace
    if not root.is_dir():
        return []

    skip = {".venv", "node_modules", "__pycache__", ".git", "staticfiles", "backups", "media", "logs"}
    results: list[str] = []
    for path in root.rglob(pattern):
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            continue
        try:
            results.append(path.relative_to(workspace).as_posix())
        except ValueError:
            continue
        if len(results) >= 2000:
            break
    return sorted(results)


# ---------------------------------------------------------------------------
# Code change proposals
# ---------------------------------------------------------------------------
def build_diff(original: str, proposed: str, file_path: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )
    )


def create_proposal(
    session: TerminalSession,
    *,
    user,
    file_path: str,
    proposed_content: str,
    title: str,
    summary: str = "",
    change_type: str = CodeChangeProposal.ChangeType.MODIFY,
) -> CodeChangeProposal:
    """Record a proposed edit. Nothing touches disk here."""
    ok, why = security.is_within_workspace(file_path)
    if not ok:
        raise ValidationError(_("Cannot modify that path: %(why)s") % {"why": why})

    path = (security.get_workspace() / file_path).resolve()
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValidationError(
            _("'%(suffix)s' files cannot be edited by the agent.") % {"suffix": path.suffix}
        )

    original = ""
    if path.exists():
        readable, content = safe_read_file(file_path)
        original = content if readable else ""
    elif change_type == CodeChangeProposal.ChangeType.MODIFY:
        change_type = CodeChangeProposal.ChangeType.CREATE

    proposal = CodeChangeProposal.objects.create(
        session=session,
        title=title[:200],
        summary=summary,
        file_path=file_path,
        change_type=change_type,
        original_content=original,
        proposed_content=proposed_content,
        unified_diff=build_diff(original, proposed_content, file_path),
        status=CodeChangeProposal.Status.AWAITING_APPROVAL,
        created_by=user,
    )
    record_audit(
        None,
        user=user,
        action=AuditAction.AI_COMMAND_PROPOSED,
        instance=proposal,
        description=_("Code change proposed for %(path)s") % {"path": file_path},
    )
    return proposal


def create_checkpoint_branch(session: TerminalSession, *, user, label: str = "") -> tuple[bool, str]:
    """Create a git branch before applying changes, so they can be abandoned.

    Best-effort: a repository without git, or with nothing committed yet, simply
    reports that no checkpoint was made rather than blocking the change.
    """
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (label or "change").lower())[:40]
    branch = f"ai/{slug}-{session.pk}"

    status = executor.execute(["git", "status", "--porcelain"], timeout=30)
    if status.error:
        return False, _("Git is not available; no checkpoint was created.")

    result = executor.execute(["git", "checkout", "-b", branch], timeout=30)
    if not result.ok:
        return False, (result.stderr or result.error or "").strip()[:200]
    return True, branch


@transaction.atomic
def apply_proposal(proposal: CodeChangeProposal, *, user) -> CodeChangeProposal:
    """Write an approved proposal to disk."""
    if not user.has_capability("ai_terminal.apply_patch"):
        raise PermissionDenied(_("You are not permitted to apply code changes."))
    if proposal.status != CodeChangeProposal.Status.APPROVED:
        raise ValidationError(_("Only approved proposals can be applied."))

    ok, why = security.is_within_workspace(proposal.file_path)
    if not ok:
        raise ValidationError(_("Path is no longer valid: %(why)s") % {"why": why})

    path = (security.get_workspace() / proposal.file_path).resolve()

    try:
        if proposal.change_type == CodeChangeProposal.ChangeType.DELETE:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Newline normalisation keeps the diff honest across platforms.
            path.write_text(proposal.proposed_content, encoding="utf-8", newline="\n")
    except OSError as exc:
        proposal.status = CodeChangeProposal.Status.FAILED
        proposal.apply_error = str(exc)[:500]
        proposal.save(update_fields=["status", "apply_error", "updated_at"])
        raise ValidationError(_("Could not write the file: %(error)s") % {"error": exc}) from exc

    proposal.mark_applied()
    record_audit(
        None,
        user=user,
        action=AuditAction.AI_ACTION,
        instance=proposal,
        description=_("AI code change applied to %(path)s (+%(add)s/-%(rem)s)")
        % {"path": proposal.file_path, "add": proposal.added_lines, "rem": proposal.removed_lines},
    )
    return proposal


@transaction.atomic
def revert_proposal(proposal: CodeChangeProposal, *, user) -> CodeChangeProposal:
    """Restore the file to the content captured before the change."""
    if not user.has_capability("ai_terminal.apply_patch"):
        raise PermissionDenied(_("You are not permitted to revert code changes."))
    if proposal.status != CodeChangeProposal.Status.APPLIED:
        raise ValidationError(_("Only an applied change can be reverted."))

    path = (security.get_workspace() / proposal.file_path).resolve()
    ok, why = security.is_within_workspace(proposal.file_path)
    if not ok:
        raise ValidationError(why)

    try:
        if proposal.change_type == CodeChangeProposal.ChangeType.CREATE:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(proposal.original_content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ValidationError(_("Could not revert: %(error)s") % {"error": exc}) from exc

    proposal.status = CodeChangeProposal.Status.REVERTED
    proposal.save(update_fields=["status", "updated_at"])
    record_audit(
        None,
        user=user,
        action=AuditAction.AI_ACTION,
        instance=proposal,
        description=_("AI code change reverted for %(path)s") % {"path": proposal.file_path},
    )
    return proposal
