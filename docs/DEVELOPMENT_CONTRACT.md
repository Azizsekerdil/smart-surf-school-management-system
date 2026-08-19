# Development Contract

**Every module in this project MUST follow this document.** It exists so that
independently written apps integrate without rework. Read it fully before
writing code. When this document and your own instinct disagree, this document
wins.

---

## 1. Environment

| Item | Value |
|---|---|
| Project root | `D:\Surf_School` |
| Python | 3.11 (`.venv\Scripts\python.exe`) |
| Django | 5.2 LTS |
| Settings | `config.settings.dev` (default), `.prod`, `.test` |
| DB | SQLite in dev, PostgreSQL in prod — **write portable ORM code only** |
| OS | Windows 11 — no POSIX-only calls, no `os.fork`, paths via `pathlib` |
| Frontend | Django templates + HTMX + Alpine.js + Tailwind (all vendored, no CDN) |

Run commands as: `.\.venv\Scripts\python.exe manage.py <cmd>`

---

## 2. App layout

Every app lives in `apps/<name>/` and contains exactly this shape:

```
apps/<name>/
    __init__.py          docstring only
    apps.py              AppConfig with default_auto_field + verbose_name
    models.py            models (see §4)
    admin.py             ModelAdmin registrations
    forms.py             ModelForms using TailwindFormMixin
    views.py             HTML views (class-based)
    urls.py              app_name = "<name>"; see §6
    api.py               DRF serializers + viewsets + ROUTES (see §7)
    services.py          business logic — NOT in views or models
    selectors.py         (optional) complex read queries
    tasks.py             (optional) Celery tasks
    signals.py           (optional) — wire in AppConfig.ready()
    tests/
        __init__.py
        test_models.py
        test_services.py
        test_views.py
        test_api.py
```

**Do NOT create `migrations/` directories.** Migrations are generated centrally
by `makemigrations` after all apps land.

**Business logic goes in `services.py`.** Views orchestrate; models validate;
services decide. A view must never contain a multi-step business rule.

---

## 3. Imports you must use

```python
from apps.core.models import BaseModel, TimeStampedModel, money_field, percent_field, AddressMixin
from apps.core.mixins import (
    HtmxPartialMixin, SearchableListMixin, DateRangeMixin,
    AuditedCreateMixin, AuditedUpdateMixin, AuditedDeleteMixin, OwnerScopedQuerysetMixin,
)
from apps.core.utils import (
    parse_date_range, previous_period, format_money, percent_change,
    to_decimal, generate_code, next_sequential_code, make_qr_png, make_qr_svg,
)
from apps.core.validators import (
    phone_validator, validate_image_upload, validate_document_upload,
    validate_latitude, validate_longitude, validate_not_negative,
)
from apps.accounts.permissions import (
    CapabilityRequiredMixin, CapabilityViewSetMixin, require_capability,
)
from apps.accounts.constants import Role
from apps.audit.services import record_audit, diff_instances
from apps.audit.models import AuditAction
from apps.core.forms_base import TailwindFormMixin   # re-exported from apps.accounts.forms
```

`apps.core` must never import from another `apps.*` package.

---

## 4. Model rules

1. Business entities inherit **`BaseModel`** (UUID `public_id`, `created_at`,
   `updated_at`, `created_by`, `updated_by`, soft delete). Lookup/reference
   tables may use `TimeStampedModel`.
2. **Money is always `money_field()`** — `DecimalField(max_digits=12, decimal_places=2)`.
   Never `FloatField` for money. Percentages use `percent_field()`.
3. Every user-visible string is wrapped: `from django.utils.translation import gettext_lazy as _`.
   This includes `verbose_name`, `help_text`, `TextChoices` labels and `Meta.verbose_name`.
4. Every model defines `Meta.verbose_name`, `Meta.verbose_name_plural`,
   `Meta.ordering`, and `__str__`.
5. Status/enum fields use `models.TextChoices` nested in the model.
6. Add `db_index=True` to any field used for filtering, and `Meta.indexes` for
   composite lookups.
7. Foreign keys: `on_delete=models.PROTECT` for records that carry money or
   history, `CASCADE` only for true child rows, `SET_NULL` for optional refs.
8. `related_name` is always explicit and plural.
9. Validation that spans fields goes in `clean()`; call `full_clean()` from
   services before saving.
10. Use `Decimal`, never `float`, for anything financial.

### 4.1 SQLite/PostgreSQL portability

- No `ArrayField`, no `JSONField` key lookups that only Postgres supports, no
  `distinct("field")`, no `search` lookups. `JSONField` itself is fine.
- Avoid `.extra()` and raw SQL. If unavoidable, guard by `connection.vendor`.
- Aggregate with `Coalesce(Sum(...), Value(Decimal("0.00")), output_field=DecimalField())`.

---

## 5. Canonical data model

Other apps depend on these **exact** names. Do not rename, and do not invent a
field on another app's model.

| App | Models |
|---|---|
| `accounts` | `User` (`role`, `language`, `get_capabilities()`, `has_capability()`) |
| `core` | `Tag`, `Note`, `Document`, `SystemSetting` |
| `audit` | `AuditLog` |
| `locations` | `SurfSpot`, `SpotHazard` |
| `customers` | `Customer`, `CustomerTag` (M2M through) |
| `students` | `Student`, `SkillAssessment` |
| `instructors` | `Instructor`, `Certification`, `AvailabilitySlot`, `TimeOff`, `PerformanceReview` |
| `lessons` | `LessonType`, `Lesson`, `LessonAttendance` |
| `bookings` | `Booking`, `WaitlistEntry` |
| `surf_camps` | `SurfCamp`, `CampParticipant`, `CampDay`, `CampActivity` |
| `equipment` | `EquipmentCategory`, `Equipment`, `EquipmentPhoto` |
| `rentals` | `Rental`, `RentalItem` |
| `maintenance` | `MaintenanceRecord`, `MaintenanceSchedule` |
| `surf_conditions` | `SurfCondition`, `SurfScore`, `ConditionForecast` |
| `safety` | `SafetyIncident`, `LifeguardAssignment`, `EmergencyContact`, `EvacuationPlan`, `EquipmentSafetyCheck`, `WeatherWarning`, `StudentRestriction` |
| `finance` | `Invoice`, `InvoiceLine`, `Payment`, `ExpenseCategory`, `Expense`, `CommissionRecord`, `PricePackage`, `CustomerPackage` |
| `pos` | `ProductCategory`, `Product`, `Sale`, `SaleItem`, `StockMovement` |
| `crm` | `Lead`, `Interaction`, `Campaign`, `Segment` |
| `notifications` | `Notification`, `NotificationTemplate`, `NotificationPreference` |
| `analytics` | `MetricSnapshot` |
| `reporting` | `ReportDefinition`, `GeneratedReport` |
| `backups` | `BackupRecord`, `RestoreRecord` |
| `ai` | `AIProviderConfig`, `AIConversation`, `AIMessage`, `AIUsageRecord`, `RagDocument`, `RagChunk` |
| `ai_terminal` | `TerminalSession`, `TerminalCommand`, `CodeChangeProposal` |
| `help_center` | `HelpCategory`, `HelpArticle` |
| `training` | `TrainingCourse`, `TrainingLesson`, `TrainingStep`, `TrainingProgress` |
| `onboarding` | `OnboardingState` |

### 5.1 Shared enumerations

Defined in `apps/core/enums.py` — **import them, never redefine**:

```python
SurfLevel      # FIRST_TIME, BEGINNER, ADVANCED_BEGINNER, INTERMEDIATE, ADVANCED, COMPETITION
BookingStatus  # DRAFT, PENDING, CONFIRMED, CHECKED_IN, COMPLETED, CANCELLED, NO_SHOW
PaymentStatus  # UNPAID, PARTIAL, PAID, REFUNDED, OVERDUE
PaymentMethod  # CASH, CARD, TRANSFER, ONLINE, PACKAGE, OTHER
EquipmentStatus# AVAILABLE, RENTED, IN_LESSON, MAINTENANCE, DAMAGED, LOST, RETIRED
EquipmentCondition # NEW, EXCELLENT, GOOD, FAIR, POOR, UNUSABLE
TideState      # LOW, MID_RISING, HIGH, MID_FALLING
WindType       # OFFSHORE, ONSHORE, CROSS_SHORE, CROSS_OFF, CROSS_ON, GLASSY
Severity       # LOW, MEDIUM, HIGH, CRITICAL
```

### 5.2 Cross-app foreign keys

Always reference by string to avoid import cycles:

```python
customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="bookings")
```

---

## 6. URLs

- `urls.py` sets `app_name = "<app_label>"` matching the namespace in
  `config/urls.py`.
- Standard route names: `list`, `detail`, `create`, `update`, `delete`,
  plus module-specific ones (`calendar`, `dashboard`, `terminal`, …).
- Detail routes use the integer PK: `path("<int:pk>/", ...)`.
- Never hard-code a URL in a template; always `{% url 'app:name' %}`.

`config/urls.py` already includes every app at a fixed prefix — check it and
match the namespace exactly.

---

## 7. REST API

Each `api.py` ends with a `ROUTES` list; `config/api_urls.py` auto-discovers it:

```python
ROUTES = [
    ("bookings", BookingViewSet, "booking"),
    ("waitlist", WaitlistViewSet, "waitlist"),
]
```

Viewsets:

```python
class BookingViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "bookings"                      # required
    capability_overrides = {"cancel": "bookings.change"}  # for @action methods
    queryset = Booking.objects.select_related("customer", "student")
    serializer_class = BookingSerializer
    filterset_fields = ["status", "booking_type"]
    search_fields = ["booking_code", "customer__first_name"]
    ordering = ["-created_at"]
```

Always `select_related` / `prefetch_related` — N+1 queries are a defect.

---

## 8. HTML views

```python
class BookingListView(CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView):
    capability = "bookings.view"
    model = Booking
    template_name = "bookings/booking_list.html"
    partial_template_name = "bookings/partials/booking_table.html"
    context_object_name = "bookings"
    paginate_by = 25
    search_fields = ("booking_code", "customer__first_name", "customer__last_name")
```

- Every view declares a `capability`.
- Create/update/delete views use the `Audited*Mixin` classes.
- List views paginate (25 default) and support `?q=`.

---

## 9. Templates

Path: `templates/<app>/<model>_<action>.html`, partials in
`templates/<app>/partials/`.

Skeleton:

```django
{% extends "base.html" %}
{% load i18n surf_tags %}

{% block title %}{% translate "Bookings" %} · {{ SCHOOL_NAME }}{% endblock %}
{% block page_title %}{% translate "Bookings" %}{% endblock %}

{% block page_actions %}
  {% can "bookings.add" as may_add %}
  {% if may_add %}
    <a href="{% url 'bookings:create' %}" class="btn-primary">
      {% icon "plus" "h-4 w-4" %} {% translate "New booking" %}
    </a>
  {% endif %}
{% endblock %}

{% block content %}
  ...
  {% include "partials/pagination.html" %}
{% endblock %}
```

Rules:
- **No hard-coded user-facing text.** Use `{% translate %}` / `{% blocktranslate %}`.
- Reuse the CSS component classes from `assets/css/input.css`: `card`,
  `btn-primary`, `table`, `badge-*`, `form-input`, `stat-card`, `alert-*`.
- Icons via `{% icon "name" "h-4 w-4" %}` (see `scripts/vendor_assets.js` for the
  vendored set; add names there if you need more).
- Guard every action with `{% can "capability" as flag %}{% if flag %}`.
- Empty lists render `partials/empty_state.html`.
- Use logical properties (`ps-*`, `pe-*`, `text-start`) not `pl-*`/`text-left`.
- Any AI-generated content is wrapped in `.ai-surface` and carries `{% ai_chip %}`.

---

## 10. Internationalisation

- Python: `from django.utils.translation import gettext_lazy as _` in models /
  forms / choices; `gettext as _` inside function bodies.
- Templates: `{% load i18n %}` then `{% translate "..." %}`.
- **Write source strings in English.** Turkish comes from the `.po` catalogue.
- Never concatenate translated fragments; use placeholders:
  `_("Booking %(code)s cancelled") % {"code": obj.code}`.

---

## 11. Security (non-negotiable)

1. Never interpolate user input into SQL. ORM only.
2. Never `mark_safe` user-supplied content. Escape by default.
3. Every state-changing HTML view is POST + CSRF.
4. File uploads go through `validate_image_upload` / `validate_document_upload`.
5. Secrets come from `django.conf.settings`, which reads the environment. Never
   log a key, never put one in a docstring, a test, a fixture or a comment.
6. Object-level access: external users (`Role.CUSTOMER`, `Role.STUDENT`) see
   only their own rows — use `OwnerScopedQuerysetMixin`.
7. Money-changing and permission-changing operations call `record_audit(...)`.
8. **The AI is never the final authority on a safety decision.** AI output about
   safety is a recommendation, labelled as such, and requires a named staff
   member to approve it.

---

## 12. Tests

- `pytest` + `pytest-django`. Settings module: `config.settings.test`.
- Use `pytest.mark.django_db`. Build objects with factories in
  `apps/<app>/tests/factories.py` (factory-boy).
- Every app tests: model `__str__`/validation, the main service function,
  one permission-denied case, and the list + detail views returning 200.
- **No test may touch the network.** Mock providers.
- Booking conflict logic, money arithmetic and capability checks require
  explicit tests.

---

## 13. Definition of done for a module

- [ ] Models complete, translated, indexed, with `__str__` and `Meta`
- [ ] `services.py` holds the business rules, with docstrings
- [ ] Admin registered with sensible `list_display`/`list_filter`/`search_fields`
- [ ] Forms styled via `TailwindFormMixin`
- [ ] HTML views: list + detail + create + update, each capability-guarded
- [ ] Templates extend `base.html` and are fully translatable
- [ ] `api.py` with serializers, viewset, and `ROUTES`
- [ ] `urls.py` with the correct `app_name`
- [ ] Tests covering models, services, permissions and views
- [ ] No `TODO`, no `pass  # placeholder`, no dead buttons, no fake data paths
