# REST API

Base path: `/api/v1/`  ·  Interactive docs: `/api/docs/`  ·  Schema: `/api/schema/`

## Authentication

| Method | Use |
|---|---|
| Session cookie | The web interface itself |
| JWT bearer token | Programmatic clients |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "..."}'

curl http://127.0.0.1:8000/api/v1/bookings/ -H "Authorization: Bearer <access>"
```

`POST /api/v1/auth/token/refresh/` rotates the pair; `/verify/` checks one.

## Authorisation

Every viewset declares a `capability_prefix`. The HTTP method maps onto an
action — GET→view, POST→add, PUT/PATCH→change, DELETE→delete — and the result
is checked against the caller's capability set, the same matrix the interface
uses. A screen can therefore never offer an action the API would refuse.

## Error envelope

Every error, from any source, returns one shape:

```json
{"error": {"type": "validation_error", "message": "…", "detail": {}}}
```

`type` is one of `validation_error`, `authentication_required`, `permission_denied`,
`not_found`, `method_not_allowed`, `conflict`, `rate_limited`, `service_unavailable`,
`internal_error`. An unexpected failure carries an `incident_id` that matches the
server log, and never leaks internals.

## Pagination, filtering, ordering

```json
{"count": 120, "total_pages": 5, "current_page": 1, "page_size": 25,
 "next": "…?page=2", "previous": null, "results": []}
```

`?page=`, `?page_size=` (max 200), `?search=`, `?ordering=-created_at`, plus each
viewset's declared filters.

## Rate limits

| Scope | Limit |
|---|---|
| Authenticated user | 2000/hour |
| Anonymous | 60/hour |
| AI endpoints | 120/hour |
| AI terminal | 60/hour |

## Endpoints

| Resource | Path | Viewset |
|---|---|---|
| `ai-conversation` | `/api/v1/ai/conversations/` | `AIConversationViewSet` |
| `ai-knowledge` | `/api/v1/ai/knowledge/` | `RagDocumentViewSet` |
| `ai-usage` | `/api/v1/ai/usage/` | `AIUsageViewSet` |
| `analytics` | `/api/v1/analytics/` | `AnalyticsViewSet` |
| `audit-log` | `/api/v1/audit/` | `AuditLogViewSet` |
| `backup-restore` | `/api/v1/backup-restores/` | `RestoreRecordViewSet` |
| `backup` | `/api/v1/backups/` | `BackupRecordViewSet` |
| `booking` | `/api/v1/bookings/` | `BookingViewSet` |
| `campactivity` | `/api/v1/camp-activities/` | `CampActivityViewSet` |
| `campday` | `/api/v1/camp-days/` | `CampDayViewSet` |
| `campparticipant` | `/api/v1/camp-participants/` | `CampParticipantViewSet` |
| `conditionforecast` | `/api/v1/condition-forecasts/` | `ConditionForecastViewSet` |
| `crm-campaign` | `/api/v1/crm/campaigns/` | `CampaignViewSet` |
| `crm-interaction` | `/api/v1/crm/interactions/` | `InteractionViewSet` |
| `crm-lead` | `/api/v1/crm/leads/` | `LeadViewSet` |
| `crm-retention` | `/api/v1/crm/retention/` | `RetentionViewSet` |
| `crm-segment` | `/api/v1/crm/segments/` | `SegmentViewSet` |
| `customer` | `/api/v1/customers/` | `CustomerViewSet` |
| `dashboard` | `/api/v1/dashboard/` | `DashboardViewSet` |
| `emergencycontact` | `/api/v1/emergency-contacts/` | `EmergencyContactViewSet` |
| `equipment` | `/api/v1/equipment/` | `EquipmentViewSet` |
| `equipment-category` | `/api/v1/equipment-categories/` | `EquipmentCategoryViewSet` |
| `equipmentsafetycheck` | `/api/v1/equipment-safety-checks/` | `EquipmentSafetyCheckViewSet` |
| `evacuationplan` | `/api/v1/evacuation-plans/` | `EvacuationPlanViewSet` |
| `finance-commission` | `/api/v1/finance/commissions/` | `CommissionRecordViewSet` |
| `finance-customer-package` | `/api/v1/finance/customer-packages/` | `CustomerPackageViewSet` |
| `finance-expense-category` | `/api/v1/finance/expense-categories/` | `ExpenseCategoryViewSet` |
| `finance-expense` | `/api/v1/finance/expenses/` | `ExpenseViewSet` |
| `finance-invoice` | `/api/v1/finance/invoices/` | `InvoiceViewSet` |
| `finance-package` | `/api/v1/finance/packages/` | `PricePackageViewSet` |
| `finance-payment` | `/api/v1/finance/payments/` | `PaymentViewSet` |
| `generatedreport` | `/api/v1/generated-reports/` | `GeneratedReportViewSet` |
| `helparticle` | `/api/v1/help-articles/` | `HelpArticleViewSet` |
| `helpcategory` | `/api/v1/help-categories/` | `HelpCategoryViewSet` |
| `instructor-availability` | `/api/v1/instructor-availability/` | `AvailabilitySlotViewSet` |
| `instructor-certification` | `/api/v1/instructor-certifications/` | `CertificationViewSet` |
| `instructor-review` | `/api/v1/instructor-reviews/` | `PerformanceReviewViewSet` |
| `instructor-time-off` | `/api/v1/instructor-time-off/` | `TimeOffViewSet` |
| `instructor` | `/api/v1/instructors/` | `InstructorViewSet` |
| `lessonattendance` | `/api/v1/lesson-attendances/` | `LessonAttendanceViewSet` |
| `lessontype` | `/api/v1/lesson-types/` | `LessonTypeViewSet` |
| `lesson` | `/api/v1/lessons/` | `LessonViewSet` |
| `lifeguardassignment` | `/api/v1/lifeguard-assignments/` | `LifeguardAssignmentViewSet` |
| `maintenance-record` | `/api/v1/maintenance/records/` | `MaintenanceRecordViewSet` |
| `maintenance-schedule` | `/api/v1/maintenance/schedules/` | `MaintenanceScheduleViewSet` |
| `metricsnapshot` | `/api/v1/metric-snapshots/` | `MetricSnapshotViewSet` |
| `notification-preference` | `/api/v1/notification-preferences/` | `NotificationPreferenceViewSet` |
| `notification-template` | `/api/v1/notification-templates/` | `NotificationTemplateViewSet` |
| `notification` | `/api/v1/notifications/` | `NotificationViewSet` |
| `pos-productcategory` | `/api/v1/pos-categories/` | `ProductCategoryViewSet` |
| `pos-product` | `/api/v1/pos-products/` | `ProductViewSet` |
| `pos-sale` | `/api/v1/pos-sales/` | `SaleViewSet` |
| `pos-stockmovement` | `/api/v1/pos-stock-movements/` | `StockMovementViewSet` |
| `rental-item` | `/api/v1/rental-items/` | `RentalItemViewSet` |
| `rental` | `/api/v1/rentals/` | `RentalViewSet` |
| `reportdefinition` | `/api/v1/report-definitions/` | `ReportDefinitionViewSet` |
| `safetygate` | `/api/v1/safety-gates/` | `SafetyGateViewSet` |
| `safetyincident` | `/api/v1/safety-incidents/` | `SafetyIncidentViewSet` |
| `skillassessment` | `/api/v1/skill-assessments/` | `SkillAssessmentViewSet` |
| `spothazard` | `/api/v1/spot-hazards/` | `SpotHazardViewSet` |
| `studentrestriction` | `/api/v1/student-restrictions/` | `StudentRestrictionViewSet` |
| `student` | `/api/v1/students/` | `StudentViewSet` |
| `surfcamp` | `/api/v1/surf-camps/` | `SurfCampViewSet` |
| `surfcondition` | `/api/v1/surf-conditions/` | `SurfConditionViewSet` |
| `surfscore` | `/api/v1/surf-scores/` | `SurfScoreViewSet` |
| `surfspot` | `/api/v1/surf-spots/` | `SurfSpotViewSet` |
| `terminal-proposal` | `/api/v1/terminal/proposals/` | `CodeChangeProposalViewSet` |
| `terminal-session` | `/api/v1/terminal/sessions/` | `TerminalSessionViewSet` |
| `trainingcourse` | `/api/v1/training-courses/` | `TrainingCourseViewSet` |
| `traininglesson` | `/api/v1/training-lessons/` | `TrainingLessonViewSet` |
| `trainingprogress` | `/api/v1/training-progress/` | `TrainingProgressViewSet` |
| `trainingstep` | `/api/v1/training-steps/` | `TrainingStepViewSet` |
| `user` | `/api/v1/users/` | `UserViewSet` |
| `waitlist` | `/api/v1/waitlist/` | `WaitlistViewSet` |
| `weatherwarning` | `/api/v1/weather-warnings/` | `WeatherWarningViewSet` |

**75 registered resources.** Each exposes the standard
list / create / retrieve / update / partial-update / destroy set unless the
viewset is read-only, plus any custom actions shown in `/api/docs/`.

## Notable custom actions

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/users/me/` | The caller with their effective capabilities |
| `GET /api/v1/users/capabilities/` | The full role/capability matrix |
| `POST /api/v1/ai/conversations/{id}/ask/` | Ask the assistant; returns the reply |
| `GET /api/v1/ai/conversations/tools/` | Tools this caller may trigger |
| `GET /api/v1/ai/conversations/health/` | Live provider health |
| `GET /api/v1/ai/knowledge/search/?q=` | Retrieval over the knowledge base |
| `POST /api/v1/terminal/sessions/{id}/propose/` | Submit a command for validation |
| `GET /api/v1/terminal/sessions/policy/` | The exact terminal security policy |

## Health

`GET /api/health/` — database, cache, Celery, LM Studio, NVIDIA, Anthropic and the
surf-data provider, with per-component latency. Anonymous callers get only
`{"status", "version"}` so the endpoint is safe to expose to a load balancer;
component detail requires authentication. Returns 503 when a critical component
is down.

## What the API deliberately does not do

There is no endpoint that executes an arbitrary terminal command in one call.
Proposing and approving are separate operations so an API client cannot collapse
the human approval gate. Code-change proposals are read-only over the API: they
are approved in the interface, where a person sees the diff first.