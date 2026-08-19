"""AI screens: assistant chat, Control Center, usage dashboard, knowledge base."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib import messages
from django.db.models import Avg, Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, ListView, TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin, require_capability
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.mixins import AuditedCreateMixin, AuditedUpdateMixin, SearchableListMixin
from apps.core.utils import parse_date_range, percent_change, previous_period

from . import rag, services, tools
from .forms import AIProviderConfigForm, ChatForm, RagDocumentForm
from .models import AIConversation, AIMessage, AIProviderConfig, AIUsageRecord, RagDocument
from .models_catalog import CATALOG
from .providers.registry import PROVIDER_CLASSES, get_provider, reset_providers
from .router import RoutingMode

logger = logging.getLogger("apps.ai")


# ---------------------------------------------------------------------------
# Assistant chat
# ---------------------------------------------------------------------------
class ChatView(CapabilityRequiredMixin, TemplateView):
    capability = "ai.view"
    template_name = "ai/chat.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        conversation_id = self.request.GET.get("c")
        conversation = services.get_or_create_conversation(
            self.request.user, int(conversation_id) if str(conversation_id).isdigit() else None
        )
        context["conversation"] = conversation
        context["chat_messages"] = conversation.messages.exclude(
            role=AIMessage.Role.SYSTEM
        ).order_by("created_at")
        context["conversations"] = AIConversation.objects.filter(
            user=self.request.user, kind=AIConversation.Kind.ASSISTANT
        )[:25]
        context["form"] = ChatForm(initial={"routing_mode": conversation.routing_mode})
        context["available_tools"] = tools.tools_for_user(self.request.user)
        context["routing_modes"] = RoutingMode.CHOICES
        context["suggestions"] = [
            _("Summarise today's lessons."),
            _("Which lesson time suits beginners tomorrow?"),
            _("Analyse the revenue trend over the last 30 days."),
            _("Show the 10 most used pieces of equipment."),
            _("Which surfboards are likely to need maintenance?"),
            _("Compare instructor performance over the last 3 months."),
            _("Are tomorrow's sea conditions suitable for beginners?"),
            _("Is there any problem with today's bookings?"),
        ]
        return context


@require_POST
def chat_send(request):
    """Handle one turn and return the rendered message pair (HTMX)."""
    require_capability(request.user, "ai.view")

    form = ChatForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "ai/partials/message_error.html",
            {"error": _("Please enter a message.")},
            status=400,
        )

    conversation_id = request.POST.get("conversation_id")
    conversation = services.get_or_create_conversation(
        request.user, int(conversation_id) if str(conversation_id).isdigit() else None
    )

    routing_mode = form.cleaned_data.get("routing_mode") or conversation.routing_mode
    if routing_mode != conversation.routing_mode:
        AIConversation.objects.filter(pk=conversation.pk).update(routing_mode=routing_mode)

    user_text = form.cleaned_data["message"]
    answer = services.run_assistant(
        request.user,
        conversation,
        user_text,
        use_rag=form.cleaned_data.get("use_rag", True),
        routing_mode=routing_mode,
    )

    return render(
        request,
        "ai/partials/message_pair.html",
        {"user_text": user_text, "message": answer, "conversation": conversation},
    )


@require_POST
def conversation_delete(request, pk: int):
    require_capability(request.user, "ai.view")
    conversation = get_object_or_404(AIConversation, pk=pk, user=request.user)
    conversation.delete()
    messages.success(request, _("Conversation deleted."))
    return redirect("ai:chat")


# ---------------------------------------------------------------------------
# AI Control Center
# ---------------------------------------------------------------------------
class ControlCenterView(CapabilityRequiredMixin, TemplateView):
    capability = "ai.manage"
    template_name = "ai/control_center.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        configs = {c.provider: c for c in AIProviderConfig.objects.all()}
        rows = []
        for name in PROVIDER_CLASSES:
            provider = get_provider(name)
            config = configs.get(name)
            rows.append(
                {
                    "name": name,
                    "label": provider.label,
                    "is_cloud": provider.is_cloud,
                    "enabled": provider.enabled,
                    "base_url": provider.base_url,
                    "has_key": bool(provider.api_key),
                    "supports_vision": provider.supports_vision,
                    "supports_tools": provider.supports_tools,
                    "supports_embeddings": provider.supports_embeddings,
                    "roles": CATALOG.get(name, {}),
                    "config": config,
                    "last_ok": config.last_health_ok if config else None,
                    "last_message": config.last_health_message if config else "",
                    "last_at": config.last_health_at if config else None,
                    "last_latency": config.last_latency_ms if config else 0,
                    "probed": (config.probed_models if config else {}) or {},
                    "monthly_spend": services.monthly_spend(name),
                    "budget": config.monthly_budget_usd if config else Decimal("0.00"),
                }
            )

        context["providers"] = rows
        context["routing_mode"] = RoutingMode.CHOICES
        context["current_mode"] = self.request.session.get("ai_routing_mode", "local_only")
        context["rag_status"] = rag.index_status()
        context["tool_count"] = len(tools.REGISTRY)
        context["month_spend"] = services.monthly_spend()
        return context


@require_POST
def test_provider(request, name: str):
    """Run a live health check against one provider (or all)."""
    require_capability(request.user, "ai.manage")

    targets = list(PROVIDER_CLASSES) if name == "all" else [name]
    results = []

    for target in targets:
        if target not in PROVIDER_CLASSES:
            continue
        try:
            provider = get_provider(target, fresh=True)
            result = provider.health_check()
        except Exception as exc:  # noqa: BLE001 - the page must still render
            logger.exception("Health check failed for %s", target)
            from .providers.base import HealthResult

            result = HealthResult(ok=False, message=f"{type(exc).__name__}: {exc}")

        config, _created = AIProviderConfig.objects.get_or_create(provider=target)
        AIProviderConfig.objects.filter(pk=config.pk).update(
            last_health_ok=result.ok,
            last_health_message=result.message[:500],
            last_health_at=timezone.now(),
            last_latency_ms=result.latency_ms,
        )
        results.append(
            {
                "name": target,
                "label": get_provider(target).label,
                "ok": result.ok,
                "message": result.message,
                "latency_ms": result.latency_ms,
                "model_count": len(result.models),
            }
        )

    record_audit(
        request,
        action=AuditAction.SYSTEM,
        description=_("AI provider health check: %(names)s") % {"names": ", ".join(targets)},
    )
    return render(request, "ai/partials/test_results.html", {"results": results})


@require_POST
def probe_models(request, name: str):
    """Actually invoke each configured model to confirm the account can use it.

    ``/v1/models`` over-reports: several listed NVIDIA models answer
    ``404 Not found for account``. Probing is the only reliable check.
    """
    require_capability(request.user, "ai.manage")

    if name not in PROVIDER_CLASSES:
        return JsonResponse({"error": "unknown provider"}, status=404)

    provider = get_provider(name, fresh=True)
    specs = CATALOG.get(name, {})
    probed: dict[str, dict] = {}

    for role, spec in specs.items():
        for model_id in [spec.model_id, *spec.fallbacks]:
            if model_id in probed:
                continue
            if hasattr(provider, "probe_model"):
                ok, message, latency = provider.probe_model(model_id, timeout=45)
            else:
                from .providers.base import ChatMessage

                response = provider.chat(
                    [ChatMessage(role="user", content="Reply with exactly: OK")],
                    model=model_id,
                    max_tokens=16,
                    temperature=0,
                    timeout=45,
                )
                ok, message, latency = response.ok, (response.error or "ok"), response.latency_ms
            probed[model_id] = {
                "ok": ok,
                "message": message[:200],
                "ms": latency,
                "role": role,
            }

    config, _created = AIProviderConfig.objects.get_or_create(provider=name)
    AIProviderConfig.objects.filter(pk=config.pk).update(probed_models=probed)

    record_audit(
        request,
        action=AuditAction.SYSTEM,
        description=_("Probed %(n)s models on %(provider)s") % {"n": len(probed), "provider": name},
    )
    return render(
        request, "ai/partials/probe_results.html", {"provider": name, "probed": probed}
    )


class ProviderConfigView(CapabilityRequiredMixin, TemplateView):
    capability = "ai.manage"
    template_name = "ai/provider_form.html"

    def get_config(self):
        config, _created = AIProviderConfig.objects.get_or_create(provider=self.kwargs["name"])
        return config

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        config = self.get_config()
        context["config"] = config
        context["provider"] = get_provider(config.provider)
        context.setdefault("form", AIProviderConfigForm(instance=config))
        context["roles"] = CATALOG.get(config.provider, {})
        return context

    def post(self, request, *args, **kwargs):
        config = self.get_config()
        form = AIProviderConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            reset_providers()  # pick up the new configuration immediately
            record_audit(
                request,
                action=AuditAction.SETTINGS_CHANGE,
                instance=config,
                description=_("AI provider %(name)s reconfigured") % {"name": config.provider},
            )
            messages.success(request, _("Provider settings saved."))
            return redirect("ai:control_center")
        return self.render_to_response(self.get_context_data(form=form))


@require_POST
def set_routing_mode(request):
    require_capability(request.user, "ai.view")
    mode = request.POST.get("mode", "auto")
    if mode in {m for m, _label in RoutingMode.CHOICES}:
        request.session["ai_routing_mode"] = mode
        messages.success(request, _("AI mode set to “%(mode)s”.") % {"mode": mode})
    return redirect(request.META.get("HTTP_REFERER") or "ai:control_center")


# ---------------------------------------------------------------------------
# Usage & cost
# ---------------------------------------------------------------------------
class UsageView(CapabilityRequiredMixin, TemplateView):
    capability = "ai.view"
    template_name = "ai/usage.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, label = parse_date_range(self.request)
        previous_start, previous_end = previous_period(start, end)

        queryset = AIUsageRecord.objects.all()
        if start:
            queryset = queryset.filter(created_at__gte=start)
        if end:
            queryset = queryset.filter(created_at__lte=end)

        totals = queryset.aggregate(
            requests=Count("id"),
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
            total=Sum("total_tokens"),
            cost=Sum("estimated_cost"),
            latency=Avg("latency_ms"),
        )

        previous_totals = {"total": 0, "cost": Decimal("0")}
        if previous_start:
            previous_totals = AIUsageRecord.objects.filter(
                created_at__gte=previous_start, created_at__lte=previous_end
            ).aggregate(total=Sum("total_tokens"), cost=Sum("estimated_cost"))

        context.update(
            {
                "range_label": label,
                "total_requests": totals["requests"] or 0,
                "prompt_tokens": totals["prompt"] or 0,
                "completion_tokens": totals["completion"] or 0,
                "total_tokens": totals["total"] or 0,
                "total_cost": totals["cost"] or Decimal("0.00"),
                "avg_latency": round(totals["latency"] or 0),
                "tokens_change": percent_change(
                    totals["total"] or 0, previous_totals.get("total") or 0
                ),
                "cost_change": percent_change(
                    totals["cost"] or 0, previous_totals.get("cost") or 0
                ),
                "by_provider": list(
                    queryset.values("provider")
                    .annotate(
                        requests=Count("id"),
                        tokens=Sum("total_tokens"),
                        cost=Sum("estimated_cost"),
                        latency=Avg("latency_ms"),
                    )
                    .order_by("-tokens")
                ),
                "by_model": list(
                    queryset.values("provider", "model")
                    .annotate(
                        requests=Count("id"), tokens=Sum("total_tokens"), cost=Sum("estimated_cost")
                    )
                    .order_by("-tokens")[:15]
                ),
                "by_operation": list(
                    queryset.values("operation")
                    .annotate(requests=Count("id"), tokens=Sum("total_tokens"))
                    .order_by("-requests")
                ),
                "by_user": list(
                    queryset.exclude(user__isnull=True)
                    .values("user__username", "user__first_name", "user__last_name")
                    .annotate(
                        requests=Count("id"), tokens=Sum("total_tokens"), cost=Sum("estimated_cost")
                    )
                    .order_by("-tokens")[:10]
                ),
                "failures": queryset.filter(was_successful=False).count(),
                "fallbacks": queryset.filter(used_fallback=True).count(),
                "local_share": queryset.filter(is_cloud=False).count(),
                "cloud_share": queryset.filter(is_cloud=True).count(),
                "daily_series": self._daily_series(queryset, start, end),
                "recent": queryset.select_related("user")[:25],
            }
        )
        return context

    @staticmethod
    def _daily_series(queryset, start, end) -> list[dict]:
        """Tokens and cost per day, for the chart."""
        from django.db.models.functions import TruncDate

        rows = (
            queryset.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(tokens=Sum("total_tokens"), cost=Sum("estimated_cost"))
            .order_by("day")
        )
        return [
            {
                "date": row["day"].isoformat() if row["day"] else "",
                "tokens": row["tokens"] or 0,
                "cost": float(row["cost"] or 0),
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Knowledge base (RAG)
# ---------------------------------------------------------------------------
class KnowledgeListView(CapabilityRequiredMixin, SearchableListMixin, ListView):
    capability = "ai.view"
    model = RagDocument
    template_name = "ai/knowledge_list.html"
    context_object_name = "documents"
    paginate_by = 25
    search_fields = ("title", "content")

    def get_queryset(self):
        return super().get_queryset().order_by("-updated_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rag_status"] = rag.index_status()
        return context


class KnowledgeCreateView(CapabilityRequiredMixin, AuditedCreateMixin, TemplateView):
    capability = "ai.change"
    template_name = "ai/knowledge_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", RagDocumentForm())
        context["title"] = _("New knowledge document")
        return context

    def post(self, request, *args, **kwargs):
        form = RagDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.created_by = request.user
            document.save()
            ok, message = rag.index_document(document)
            messages.success(request, _("Document saved.")) if ok else messages.warning(
                request, _("Saved, but indexing failed: %(msg)s") % {"msg": message}
            )
            record_audit(request, action=AuditAction.CREATE, instance=document)
            return redirect("ai:knowledge_list")
        return self.render_to_response(self.get_context_data(form=form))


class KnowledgeUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, TemplateView):
    capability = "ai.change"
    template_name = "ai/knowledge_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = get_object_or_404(RagDocument, pk=self.kwargs["pk"])
        context["document"] = document
        context.setdefault("form", RagDocumentForm(instance=document))
        context["title"] = _("Edit knowledge document")
        return context

    def post(self, request, *args, **kwargs):
        document = get_object_or_404(RagDocument, pk=self.kwargs["pk"])
        form = RagDocumentForm(request.POST, request.FILES, instance=document)
        if form.is_valid():
            document = form.save()
            rag.index_document(document, force=True)
            record_audit(request, action=AuditAction.UPDATE, instance=document)
            messages.success(request, _("Document updated and re-indexed."))
            return redirect("ai:knowledge_list")
        return self.render_to_response(self.get_context_data(form=form))


class KnowledgeDeleteView(CapabilityRequiredMixin, DeleteView):
    capability = "ai.delete"
    model = RagDocument
    template_name = "ai/knowledge_confirm_delete.html"
    success_url = reverse_lazy("ai:knowledge_list")
    context_object_name = "document"


@require_POST
def reindex_knowledge(request):
    require_capability(request.user, "ai.change")
    report = rag.reindex_all(force=True)
    if report["failed"]:
        messages.warning(
            request,
            _("Re-indexed %(ok)s document(s); %(bad)s failed. %(errors)s")
            % {
                "ok": report["indexed"],
                "bad": report["failed"],
                "errors": "; ".join(report["errors"][:3]),
            },
        )
    else:
        messages.success(
            request, _("Re-indexed %(ok)s document(s).") % {"ok": report["indexed"]}
        )
    record_audit(
        request, action=AuditAction.SYSTEM, description=_("Knowledge base re-indexed")
    )
    return redirect("ai:knowledge_list")


@require_POST
def search_knowledge(request):
    """HTMX endpoint used by the knowledge screen to test retrieval."""
    require_capability(request.user, "ai.view")
    query = request.POST.get("query", "").strip()
    hits = rag.search(query, limit=8) if query else []
    return render(request, "ai/partials/search_results.html", {"hits": hits, "query": query})
