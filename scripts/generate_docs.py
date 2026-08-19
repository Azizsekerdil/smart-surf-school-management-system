"""Generate docs/DATABASE.md and docs/API.md by introspecting the real project.

Hand-written schema documentation drifts. These two are produced from the live
model registry and the DRF router, so they describe what actually exists.
"""

import os
import sys
from pathlib import Path

BASE = Path(r"D:\Surf_School")
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

django.setup()

from django.apps import apps as django_apps
from django.db import models

LOCAL = [c for c in django_apps.get_app_configs() if c.name.startswith("apps.")]

# ---------------------------------------------------------------------------
# DATABASE.md
# ---------------------------------------------------------------------------
lines = [
    "# Database",
    "",
    "Generated from the live model registry — see `scripts/` for the generator.",
    "",
    "## Engines",
    "",
    "| Environment | Engine | Notes |",
    "|---|---|---|",
    "| Development / test | SQLite | WAL journalling, `foreign_keys=ON`, 20 s busy timeout |",
    "| Production | PostgreSQL | Set `DATABASE_URL=postgres://…`; no code change |",
    "",
    "The ORM is written portably: no `ArrayField`, no PostgreSQL-only lookups, no",
    "`distinct('field')`, no raw SQL. Aggregates use `Coalesce(..., Value(Decimal('0.00')))`",
    "so an empty table returns zero rather than `None` on both engines.",
    "",
    "## Conventions",
    "",
    "- Business entities inherit `BaseModel`: integer PK for joins, a non-guessable",
    "  `public_id` UUID for URLs and QR codes, `created_at` / `updated_at`,",
    "  `created_by` / `updated_by`, and soft delete (`is_deleted`, `deleted_at`).",
    "- Soft-deleted rows are hidden from `.objects` and reachable through `.all_objects`.",
    "- Money is always `DecimalField(max_digits=12, decimal_places=2)` via `money_field()`.",
    "- Cross-app foreign keys are declared as strings (`\"customers.Customer\"`).",
    "- Foreign keys carrying money or history use `PROTECT`; true child rows use",
    "  `CASCADE`; optional references use `SET_NULL`.",
    "",
    "## Schema",
    "",
]

total_models = 0
total_fields = 0

for config in sorted(LOCAL, key=lambda c: c.label):
    app_models = sorted(config.get_models(), key=lambda m: m.__name__)
    if not app_models:
        continue
    lines.append(f"### `{config.label}`")
    lines.append("")
    for model in app_models:
        total_models += 1
        meta = model._meta
        lines.append(f"#### {model.__name__}")
        verbose = str(meta.verbose_name)
        lines.append("")
        lines.append(f"*{verbose}* — table `{meta.db_table}`")
        lines.append("")
        lines.append("| Field | Type | Null | Notes |")
        lines.append("|---|---|---|---|")
        for field in meta.get_fields():
            if not getattr(field, "concrete", False):
                continue
            total_fields += 1
            kind = field.get_internal_type()
            notes = []
            if getattr(field, "primary_key", False):
                notes.append("PK")
            if getattr(field, "unique", False) and not field.primary_key:
                notes.append("unique")
            if getattr(field, "db_index", False):
                notes.append("indexed")
            if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                target = field.related_model
                label = f"{target._meta.app_label}.{target.__name__}" if target else "?"
                on_delete = getattr(field.remote_field, "on_delete", None)
                notes.append(f"→ {label}")
                if on_delete is not None:
                    notes.append(getattr(on_delete, "__name__", str(on_delete)).upper())
            if isinstance(field, models.DecimalField):
                notes.append(f"{field.max_digits},{field.decimal_places}")
            if getattr(field, "choices", None):
                notes.append(f"{len(field.choices)} choices")
            lines.append(
                f"| `{field.name}` | {kind} | {'yes' if field.null else 'no'} | "
                f"{', '.join(notes)} |"
            )
        if meta.indexes:
            lines.append("")
            lines.append(
                "Composite indexes: "
                + ", ".join("(" + ", ".join(i.fields) + ")" for i in meta.indexes)
            )
        if meta.constraints:
            lines.append("")
            lines.append(
                "Constraints: " + ", ".join(getattr(c, "name", str(c)) for c in meta.constraints)
            )
        lines.append("")
    lines.append("")

lines.insert(
    4,
    f"**{total_models} models across {len([c for c in LOCAL if list(c.get_models())])} apps, "
    f"{total_fields} concrete fields.**\n",
)

(BASE / "docs" / "DATABASE.md").write_text("\n".join(lines), encoding="utf-8")
print(f"DATABASE.md: {total_models} models, {total_fields} fields")

# ---------------------------------------------------------------------------
# API.md
# ---------------------------------------------------------------------------
from config.api_urls import router

api = [
    "# REST API",
    "",
    "Base path: `/api/v1/`  ·  Interactive docs: `/api/docs/`  ·  Schema: `/api/schema/`",
    "",
    "## Authentication",
    "",
    "| Method | Use |",
    "|---|---|",
    "| Session cookie | The web interface itself |",
    "| JWT bearer token | Programmatic clients |",
    "",
    "```bash",
    "curl -X POST http://127.0.0.1:8000/api/v1/auth/token/ \\",
    '  -H "Content-Type: application/json" \\',
    '  -d \'{"username": "admin", "password": "..."}\'',
    "",
    'curl http://127.0.0.1:8000/api/v1/bookings/ -H "Authorization: Bearer <access>"',
    "```",
    "",
    "`POST /api/v1/auth/token/refresh/` rotates the pair; `/verify/` checks one.",
    "",
    "## Authorisation",
    "",
    "Every viewset declares a `capability_prefix`. The HTTP method maps onto an",
    "action — GET→view, POST→add, PUT/PATCH→change, DELETE→delete — and the result",
    "is checked against the caller's capability set, the same matrix the interface",
    "uses. A screen can therefore never offer an action the API would refuse.",
    "",
    "## Error envelope",
    "",
    "Every error, from any source, returns one shape:",
    "",
    "```json",
    '{"error": {"type": "validation_error", "message": "…", "detail": {}}}',
    "```",
    "",
    "`type` is one of `validation_error`, `authentication_required`, `permission_denied`,",
    "`not_found`, `method_not_allowed`, `conflict`, `rate_limited`, `service_unavailable`,",
    "`internal_error`. An unexpected failure carries an `incident_id` that matches the",
    "server log, and never leaks internals.",
    "",
    "## Pagination, filtering, ordering",
    "",
    "```json",
    '{"count": 120, "total_pages": 5, "current_page": 1, "page_size": 25,',
    ' "next": "…?page=2", "previous": null, "results": []}',
    "```",
    "",
    "`?page=`, `?page_size=` (max 200), `?search=`, `?ordering=-created_at`, plus each",
    "viewset's declared filters.",
    "",
    "## Rate limits",
    "",
    "| Scope | Limit |",
    "|---|---|",
    "| Authenticated user | 2000/hour |",
    "| Anonymous | 60/hour |",
    "| AI endpoints | 120/hour |",
    "| AI terminal | 60/hour |",
    "",
    "## Endpoints",
    "",
    "| Resource | Path | Viewset |",
    "|---|---|---|",
]

for prefix, viewset, basename in sorted(router.registry, key=lambda r: r[0]):
    api.append(f"| `{basename}` | `/api/v1/{prefix}/` | `{viewset.__name__}` |")

api += [
    "",
    f"**{len(router.registry)} registered resources.** Each exposes the standard",
    "list / create / retrieve / update / partial-update / destroy set unless the",
    "viewset is read-only, plus any custom actions shown in `/api/docs/`.",
    "",
    "## Notable custom actions",
    "",
    "| Endpoint | Purpose |",
    "|---|---|",
    "| `GET /api/v1/users/me/` | The caller with their effective capabilities |",
    "| `GET /api/v1/users/capabilities/` | The full role/capability matrix |",
    "| `POST /api/v1/ai/conversations/{id}/ask/` | Ask the assistant; returns the reply |",
    "| `GET /api/v1/ai/conversations/tools/` | Tools this caller may trigger |",
    "| `GET /api/v1/ai/conversations/health/` | Live provider health |",
    "| `GET /api/v1/ai/knowledge/search/?q=` | Retrieval over the knowledge base |",
    "| `POST /api/v1/terminal/sessions/{id}/propose/` | Submit a command for validation |",
    "| `GET /api/v1/terminal/sessions/policy/` | The exact terminal security policy |",
    "",
    "## Health",
    "",
    "`GET /api/health/` — database, cache, Celery, LM Studio, NVIDIA, Anthropic and the",
    "surf-data provider, with per-component latency. Anonymous callers get only",
    "`{\"status\", \"version\"}` so the endpoint is safe to expose to a load balancer;",
    "component detail requires authentication. Returns 503 when a critical component",
    "is down.",
    "",
    "## What the API deliberately does not do",
    "",
    "There is no endpoint that executes an arbitrary terminal command in one call.",
    "Proposing and approving are separate operations so an API client cannot collapse",
    "the human approval gate. Code-change proposals are read-only over the API: they",
    "are approved in the interface, where a person sees the diff first.",
]

(BASE / "docs" / "API.md").write_text("\n".join(api), encoding="utf-8")
print(f"API.md: {len(router.registry)} resources")
