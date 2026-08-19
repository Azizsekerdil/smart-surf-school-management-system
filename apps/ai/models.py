"""AI data models: provider overrides, conversations, usage accounting and RAG."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, TimeStampedModel, money_field


class AIProviderConfig(TimeStampedModel):
    """Runtime overrides for a provider, editable from the AI Control Center.

    Credentials are **not** stored here: the API key always comes from the
    environment. This table only holds non-secret operational settings, so a
    database dump can never leak a key.
    """

    provider = models.CharField(_("provider"), max_length=32, unique=True)
    is_enabled = models.BooleanField(_("enabled"), default=True)
    base_url_override = models.CharField(_("base URL override"), max_length=300, blank=True)
    model_overrides = models.JSONField(
        _("model overrides"),
        default=dict,
        blank=True,
        help_text=_('Role → model id, e.g. {"assistant": "nvidia/nemotron-3-super-120b-a12b"}'),
    )
    monthly_budget_usd = money_field(
        _("monthly budget (USD)"),
        default=Decimal("0.00"),
        help_text=_("0 means no limit. Requests are blocked once the budget is reached."),
    )
    last_health_ok = models.BooleanField(_("last health check passed"), default=False)
    last_health_message = models.CharField(_("last health message"), max_length=500, blank=True)
    last_health_at = models.DateTimeField(_("last checked"), null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(_("last latency (ms)"), default=0)
    #: Result of the "probe models" action: {model_id: {"ok": bool, "message": str, "ms": int}}
    probed_models = models.JSONField(_("probed models"), default=dict, blank=True)

    class Meta:
        verbose_name = _("AI provider configuration")
        verbose_name_plural = _("AI provider configurations")
        ordering = ["provider"]

    def __str__(self) -> str:
        return self.provider

    def as_config_overlay(self) -> dict:
        """Non-secret settings merged over the environment configuration."""
        overlay: dict = {"ENABLED": self.is_enabled}
        if self.base_url_override:
            overlay["BASE_URL"] = self.base_url_override
        if self.model_overrides:
            overlay["MODELS"] = dict(self.model_overrides)
        return overlay


class AIConversation(BaseModel):
    """A chat thread between a user and the assistant."""

    class Kind(models.TextChoices):
        ASSISTANT = "assistant", _("Business assistant")
        TERMINAL = "terminal", _("Development terminal")
        ANALYSIS = "analysis", _("Analysis")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_conversations",
        verbose_name=_("user"),
    )
    title = models.CharField(_("title"), max_length=200, blank=True)
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices, default=Kind.ASSISTANT)
    routing_mode = models.CharField(_("routing mode"), max_length=16, default="local_only")
    is_pinned = models.BooleanField(_("pinned"), default=False)
    total_tokens = models.PositiveIntegerField(_("total tokens"), default=0)
    total_cost = money_field(_("total cost (USD)"), default=Decimal("0.00"), decimal_places=6)

    class Meta:
        verbose_name = _("AI conversation")
        verbose_name_plural = _("AI conversations")
        ordering = ["-is_pinned", "-updated_at"]
        indexes = [models.Index(fields=["user", "-updated_at"])]

    def __str__(self) -> str:
        return self.title or _("Untitled conversation")

    @property
    def message_count(self) -> int:
        return self.messages.count()


class AIMessage(TimeStampedModel):
    """One turn in a conversation."""

    class Role(models.TextChoices):
        SYSTEM = "system", _("System")
        USER = "user", _("User")
        ASSISTANT = "assistant", _("Assistant")
        TOOL = "tool", _("Tool result")

    conversation = models.ForeignKey(
        AIConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(_("role"), max_length=16, choices=Role.choices)
    content = models.TextField(_("content"))
    #: Chain-of-thought when the model exposes it — displayed collapsed and
    #: never mixed into the answer.
    reasoning = models.TextField(_("reasoning"), blank=True)
    #: Tool calls requested by the model, and the names actually executed.
    tool_calls = models.JSONField(_("tool calls"), default=list, blank=True)
    tool_name = models.CharField(_("tool name"), max_length=100, blank=True)

    provider = models.CharField(_("provider"), max_length=32, blank=True)
    model = models.CharField(_("model"), max_length=120, blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    used_fallback = models.BooleanField(default=False)
    error = models.CharField(_("error"), max_length=500, blank=True)
    #: Sources returned by RAG, so an answer can always be traced to evidence.
    citations = models.JSONField(_("citations"), default=list, blank=True)

    class Meta:
        verbose_name = _("AI message")
        verbose_name_plural = _("AI messages")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:60]}"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AIUsageRecord(TimeStampedModel):
    """Immutable per-request accounting row.

    Every AI call — assistant, terminal, RAG indexing, background analysis —
    writes one of these, so the cost dashboard can never understate usage.
    """

    class Operation(models.TextChoices):
        CHAT = "chat", _("Chat")
        TOOL_CALL = "tool_call", _("Tool call")
        EMBEDDING = "embedding", _("Embedding")
        ANALYSIS = "analysis", _("Analysis")
        TERMINAL = "terminal", _("Development terminal")
        TRANSLATION = "translation", _("Translation")
        VISION = "vision", _("Vision")
        HEALTH_CHECK = "health_check", _("Health check")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_usage",
        verbose_name=_("user"),
    )
    conversation = models.ForeignKey(
        AIConversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage"
    )
    provider = models.CharField(_("provider"), max_length=32, db_index=True)
    model = models.CharField(_("model"), max_length=120, db_index=True)
    role = models.CharField(_("role"), max_length=32, blank=True)
    operation = models.CharField(
        _("operation"), max_length=16, choices=Operation.choices, default=Operation.CHAT
    )
    is_cloud = models.BooleanField(_("cloud"), default=False, db_index=True)

    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0, db_index=True)
    estimated_cost = models.DecimalField(
        _("estimated cost (USD)"), max_digits=12, decimal_places=6, default=Decimal("0.000000")
    )
    latency_ms = models.PositiveIntegerField(default=0)
    was_successful = models.BooleanField(default=True)
    used_fallback = models.BooleanField(default=False)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = _("AI usage record")
        verbose_name_plural = _("AI usage records")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["provider", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider}/{self.model} · {self.total_tokens} tokens"

    def save(self, *args, **kwargs):
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------
class RagDocument(BaseModel):
    """A source document the assistant may ground its answers in."""

    class Source(models.TextChoices):
        MANUAL = "manual", _("Manual")
        POLICY = "policy", _("Policy")
        TRAINING = "training", _("Training material")
        SAFETY = "safety", _("Safety procedure")
        EQUIPMENT = "equipment", _("Equipment manual")
        INTERNAL = "internal", _("Internal document")
        HELP = "help", _("Help article")
        DATABASE = "database", _("Database record")

    title = models.CharField(_("title"), max_length=250)
    source_type = models.CharField(
        _("type"), max_length=20, choices=Source.choices, default=Source.INTERNAL
    )
    language = models.CharField(_("language"), max_length=5, default="tr")
    content = models.TextField(_("content"))
    file = models.FileField(_("file"), upload_to="rag/%Y/%m/", blank=True, null=True)
    source_url = models.CharField(_("source"), max_length=500, blank=True)
    checksum = models.CharField(_("checksum"), max_length=64, blank=True, db_index=True)
    is_indexed = models.BooleanField(_("indexed"), default=False, db_index=True)
    indexed_at = models.DateTimeField(_("indexed at"), null=True, blank=True)
    chunk_count = models.PositiveIntegerField(_("chunks"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("knowledge document")
        verbose_name_plural = _("knowledge base")
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title

    def compute_checksum(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        new_checksum = self.compute_checksum()
        if new_checksum != self.checksum:
            # Content changed — the existing index is stale.
            self.checksum = new_checksum
            self.is_indexed = False
        super().save(*args, **kwargs)


class RagChunk(TimeStampedModel):
    """One embedded passage.

    The embedding model and its dimension are stored per chunk: an index built
    with a 2048-dimension model cannot be searched with a 768-dimension one, and
    silently mixing them produces confidently wrong retrieval.
    """

    document = models.ForeignKey(RagDocument, on_delete=models.CASCADE, related_name="chunks")
    chunk_index = models.PositiveIntegerField(_("position"), default=0)
    content = models.TextField(_("content"))
    token_estimate = models.PositiveIntegerField(default=0)

    embedding = models.JSONField(_("embedding"), default=list)
    embedding_model = models.CharField(_("embedding model"), max_length=120, blank=True, db_index=True)
    embedding_dimensions = models.PositiveIntegerField(_("dimensions"), default=0, db_index=True)
    #: Pre-computed L2 norm so cosine similarity is one dot product at query time.
    embedding_norm = models.FloatField(default=0.0)

    class Meta:
        verbose_name = _("knowledge chunk")
        verbose_name_plural = _("knowledge chunks")
        ordering = ["document", "chunk_index"]
        indexes = [models.Index(fields=["embedding_model", "embedding_dimensions"])]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"], name="unique_chunk_position"
            )
        ]

    def __str__(self) -> str:
        return f"{self.document.title} #{self.chunk_index}"
