"""REST API for the AI terminal.

Note what is *not* here: there is no endpoint that executes an arbitrary command
string in one call. Proposing and approving are separate operations by design,
so an API client cannot collapse the human approval gate.
"""

from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import security, services
from .models import CodeChangeProposal, TerminalCommand, TerminalSession


class TerminalCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = TerminalCommand
        fields = [
            "id", "command", "argv", "origin", "rationale", "risk", "policy_rule",
            "policy_reason", "status", "exit_code", "stdout", "stderr",
            "duration_ms", "output_truncated", "executed_at", "created_at",
        ]
        read_only_fields = [
            "argv", "risk", "policy_rule", "policy_reason", "status", "exit_code",
            "stdout", "stderr", "duration_ms", "output_truncated", "executed_at",
        ]


class TerminalSessionSerializer(serializers.ModelSerializer):
    commands = TerminalCommandSerializer(many=True, read_only=True)

    class Meta:
        model = TerminalSession
        fields = ["id", "title", "goal", "is_active", "created_at", "updated_at", "commands"]


class TerminalSessionViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "ai_terminal"
    capability_overrides = {"propose": "ai_terminal.execute", "policy": "ai_terminal.view"}
    serializer_class = TerminalSessionSerializer

    def get_queryset(self):
        return TerminalSession.objects.filter(user=self.request.user).prefetch_related("commands")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def propose(self, request, pk=None):
        """Submit a command for validation.

        Safe commands run immediately; anything else comes back with status
        ``awaiting_approval`` and must be approved in the UI by a user holding
        ``ai_terminal.approve``.
        """
        session = self.get_object()
        command = (request.data.get("command") or "").strip()
        if not command:
            return Response(
                {"error": {"type": "validation_error", "message": "command is required", "detail": {}}},
                status=400,
            )
        entry = services.propose_and_maybe_run(
            session,
            command,
            user=request.user,
            rationale=request.data.get("rationale", ""),
        )
        return Response(TerminalCommandSerializer(entry).data)

    @action(detail=False, methods=["get"])
    def policy(self, request):
        """The exact policy the terminal enforces."""
        return Response(security.describe_policy())


class CodeChangeProposalSerializer(serializers.ModelSerializer):
    added_lines = serializers.IntegerField(read_only=True)
    removed_lines = serializers.IntegerField(read_only=True)

    class Meta:
        model = CodeChangeProposal
        fields = [
            "id", "title", "summary", "file_path", "change_type", "unified_diff",
            "status", "added_lines", "removed_lines", "applied_at",
            "checkpoint_branch", "created_at",
        ]
        read_only_fields = fields


class CodeChangeProposalViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only over the API: approving and applying happen in the UI, where a
    human sees the diff before deciding."""

    capability_prefix = "ai_terminal"
    queryset = CodeChangeProposal.objects.select_related("session")
    serializer_class = CodeChangeProposalSerializer
    filterset_fields = ["status", "change_type"]
    ordering = ["-created_at"]


ROUTES = [
    ("terminal/sessions", TerminalSessionViewSet, "terminal-session"),
    ("terminal/proposals", CodeChangeProposalViewSet, "terminal-proposal"),
]
