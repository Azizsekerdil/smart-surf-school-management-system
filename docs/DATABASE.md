# Database

Generated from the live model registry — see `scripts/` for the generator.

**86 models across 27 apps, 1597 concrete fields.**

## Engines

| Environment | Engine | Notes |
|---|---|---|
| Development / test | SQLite | WAL journalling, `foreign_keys=ON`, 20 s busy timeout |
| Production | PostgreSQL | Set `DATABASE_URL=postgres://…`; no code change |

The ORM is written portably: no `ArrayField`, no PostgreSQL-only lookups, no
`distinct('field')`, no raw SQL. Aggregates use `Coalesce(..., Value(Decimal('0.00')))`
so an empty table returns zero rather than `None` on both engines.

## Conventions

- Business entities inherit `BaseModel`: integer PK for joins, a non-guessable
  `public_id` UUID for URLs and QR codes, `created_at` / `updated_at`,
  `created_by` / `updated_by`, and soft delete (`is_deleted`, `deleted_at`).
- Soft-deleted rows are hidden from `.objects` and reachable through `.all_objects`.
- Money is always `DecimalField(max_digits=12, decimal_places=2)` via `money_field()`.
- Cross-app foreign keys are declared as strings (`"customers.Customer"`).
- Foreign keys carrying money or history use `PROTECT`; true child rows use
  `CASCADE`; optional references use `SET_NULL`.

## Schema

### `accounts`

#### PasswordResetRequest

*password reset request* — table `accounts_passwordresetrequest`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `user` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `requested_ip` | GenericIPAddressField | yes |  |
| `used_at` | DateTimeField | yes |  |

#### User

*kullanıcı* — table `accounts_user`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `password` | CharField | no |  |
| `last_login` | DateTimeField | yes |  |
| `is_superuser` | BooleanField | no |  |
| `username` | CharField | no | unique |
| `first_name` | CharField | no |  |
| `last_name` | CharField | no |  |
| `is_staff` | BooleanField | no |  |
| `is_active` | BooleanField | no |  |
| `date_joined` | DateTimeField | no |  |
| `email` | CharField | no | unique |
| `role` | CharField | no | indexed, 15 choices |
| `phone` | CharField | no |  |
| `photo` | FileField | yes |  |
| `employee_id` | CharField | no |  |
| `language` | CharField | no | 2 choices |
| `job_title` | CharField | no |  |
| `extra_capabilities` | JSONField | no |  |
| `denied_capabilities` | JSONField | no |  |
| `must_change_password` | BooleanField | no |  |
| `last_seen_at` | DateTimeField | yes |  |
| `notes` | TextField | no |  |
| `groups` | ManyToManyField | no |  |
| `user_permissions` | ManyToManyField | no |  |

Composite indexes: (role, is_active)

#### UserSession

*user session* — table `accounts_usersession`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `user` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `session_key` | CharField | no | indexed |
| `ip_address` | GenericIPAddressField | yes |  |
| `user_agent` | CharField | no |  |
| `login_at` | DateTimeField | no | indexed |
| `logout_at` | DateTimeField | yes |  |
| `was_successful` | BooleanField | no |  |


### `ai`

#### AIConversation

*AI conversation* — table `ai_aiconversation`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `user` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `title` | CharField | no |  |
| `kind` | CharField | no | 3 choices |
| `routing_mode` | CharField | no |  |
| `is_pinned` | BooleanField | no |  |
| `total_tokens` | PositiveIntegerField | no |  |
| `total_cost` | DecimalField | no | 12,6 |

Composite indexes: (user, -updated_at)

#### AIMessage

*AI message* — table `ai_aimessage`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `conversation` | ForeignKey | no | indexed, → ai.AIConversation, CASCADE |
| `role` | CharField | no | 4 choices |
| `content` | TextField | no |  |
| `reasoning` | TextField | no |  |
| `tool_calls` | JSONField | no |  |
| `tool_name` | CharField | no |  |
| `provider` | CharField | no |  |
| `model` | CharField | no |  |
| `prompt_tokens` | PositiveIntegerField | no |  |
| `completion_tokens` | PositiveIntegerField | no |  |
| `latency_ms` | PositiveIntegerField | no |  |
| `used_fallback` | BooleanField | no |  |
| `error` | CharField | no |  |
| `citations` | JSONField | no |  |

#### AIProviderConfig

*AI provider configuration* — table `ai_aiproviderconfig`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `provider` | CharField | no | unique |
| `is_enabled` | BooleanField | no |  |
| `base_url_override` | CharField | no |  |
| `model_overrides` | JSONField | no |  |
| `monthly_budget_usd` | DecimalField | no | 12,2 |
| `last_health_ok` | BooleanField | no |  |
| `last_health_message` | CharField | no |  |
| `last_health_at` | DateTimeField | yes |  |
| `last_latency_ms` | PositiveIntegerField | no |  |
| `probed_models` | JSONField | no |  |

#### AIUsageRecord

*AI usage record* — table `ai_aiusagerecord`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `user` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `conversation` | ForeignKey | yes | indexed, → ai.AIConversation, SET_NULL |
| `provider` | CharField | no | indexed |
| `model` | CharField | no | indexed |
| `role` | CharField | no |  |
| `operation` | CharField | no | 8 choices |
| `is_cloud` | BooleanField | no | indexed |
| `prompt_tokens` | PositiveIntegerField | no |  |
| `completion_tokens` | PositiveIntegerField | no |  |
| `total_tokens` | PositiveIntegerField | no | indexed |
| `estimated_cost` | DecimalField | no | 12,6 |
| `latency_ms` | PositiveIntegerField | no |  |
| `was_successful` | BooleanField | no |  |
| `used_fallback` | BooleanField | no |  |
| `error` | CharField | no |  |

Composite indexes: (-created_at), (provider, -created_at), (user, -created_at)

#### RagChunk

*knowledge chunk* — table `ai_ragchunk`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `document` | ForeignKey | no | indexed, → ai.RagDocument, CASCADE |
| `chunk_index` | PositiveIntegerField | no |  |
| `content` | TextField | no |  |
| `token_estimate` | PositiveIntegerField | no |  |
| `embedding` | JSONField | no |  |
| `embedding_model` | CharField | no | indexed |
| `embedding_dimensions` | PositiveIntegerField | no | indexed |
| `embedding_norm` | FloatField | no |  |

Composite indexes: (embedding_model, embedding_dimensions)

Constraints: unique_chunk_position

#### RagDocument

*knowledge document* — table `ai_ragdocument`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `title` | CharField | no |  |
| `source_type` | CharField | no | 8 choices |
| `language` | CharField | no |  |
| `content` | TextField | no |  |
| `file` | FileField | yes |  |
| `source_url` | CharField | no |  |
| `checksum` | CharField | no | indexed |
| `is_indexed` | BooleanField | no | indexed |
| `indexed_at` | DateTimeField | yes |  |
| `chunk_count` | PositiveIntegerField | no |  |
| `is_active` | BooleanField | no |  |


### `ai_terminal`

#### CodeChangeProposal

*code change proposal* — table `ai_terminal_codechangeproposal`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `session` | ForeignKey | no | indexed, → ai_terminal.TerminalSession, CASCADE |
| `title` | CharField | no |  |
| `summary` | TextField | no |  |
| `file_path` | CharField | no |  |
| `change_type` | CharField | no | 3 choices |
| `original_content` | TextField | no |  |
| `proposed_content` | TextField | no |  |
| `unified_diff` | TextField | no |  |
| `status` | CharField | no | indexed, 7 choices |
| `approved_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `decided_at` | DateTimeField | yes |  |
| `decision_note` | CharField | no |  |
| `applied_at` | DateTimeField | yes |  |
| `apply_error` | CharField | no |  |
| `checkpoint_branch` | CharField | no |  |

#### TerminalCommand

*terminal command* — table `ai_terminal_terminalcommand`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `session` | ForeignKey | no | indexed, → ai_terminal.TerminalSession, CASCADE |
| `origin` | CharField | no | 2 choices |
| `command` | TextField | no |  |
| `argv` | JSONField | no |  |
| `rationale` | TextField | no |  |
| `risk` | CharField | no |  |
| `policy_rule` | CharField | no |  |
| `policy_reason` | CharField | no |  |
| `status` | CharField | no | indexed, 10 choices |
| `requested_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `approved_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `decided_at` | DateTimeField | yes |  |
| `decision_note` | CharField | no |  |
| `edited_command` | TextField | no |  |
| `exit_code` | IntegerField | yes |  |
| `stdout` | TextField | no |  |
| `stderr` | TextField | no |  |
| `duration_ms` | PositiveIntegerField | no |  |
| `output_truncated` | BooleanField | no |  |
| `executed_at` | DateTimeField | yes |  |

Composite indexes: (session, created_at)

#### TerminalSession

*terminal session* — table `ai_terminal_terminalsession`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `user` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `title` | CharField | no |  |
| `goal` | TextField | no |  |
| `is_active` | BooleanField | no |  |
| `closed_at` | DateTimeField | yes |  |


### `analytics`

#### MetricSnapshot

*metric snapshot* — table `analytics_metricsnapshot`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `metric_key` | CharField | no | indexed |
| `period_start` | DateField | no | indexed |
| `period_end` | DateField | no |  |
| `granularity` | CharField | no | indexed, 4 choices |
| `value` | DecimalField | no | 14,4 |
| `count` | PositiveIntegerField | no |  |
| `dimensions` | JSONField | no |  |
| `computed_at` | DateTimeField | no | indexed |

Composite indexes: (metric_key, granularity, period_start), (metric_key, -computed_at)

Constraints: uniq_metric_snapshot_window, metric_snapshot_period_ordered


### `audit`

#### AuditLog

*audit entry* — table `audit_auditlog`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `user` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `username` | CharField | no | indexed |
| `user_role` | CharField | no |  |
| `action` | CharField | no | indexed, 28 choices |
| `description` | TextField | no |  |
| `changes` | JSONField | no |  |
| `content_type` | ForeignKey | yes | indexed, → contenttypes.ContentType, SET_NULL |
| `object_id` | CharField | no | indexed |
| `object_repr` | CharField | no |  |
| `source` | CharField | no | 7 choices |
| `ip_address` | GenericIPAddressField | yes |  |
| `user_agent` | CharField | no |  |
| `request_path` | CharField | no |  |
| `request_id` | CharField | no | indexed |
| `created_at` | DateTimeField | no | indexed |
| `is_sensitive` | BooleanField | no |  |

Composite indexes: (-created_at), (action, -created_at), (content_type, object_id), (user, -created_at)


### `backups`

#### BackupRecord

*backup* — table `backups_backuprecord`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `backup_code` | CharField | no | unique, indexed |
| `backup_type` | CharField | no | indexed, 5 choices |
| `scope` | CharField | no | indexed, 4 choices |
| `status` | CharField | no | indexed, 5 choices |
| `file_path` | CharField | no |  |
| `file_size_bytes` | BigIntegerField | no |  |
| `checksum_sha256` | CharField | no | indexed |
| `database_engine` | CharField | no |  |
| `django_version` | CharField | no |  |
| `app_version` | CharField | no |  |
| `started_at` | DateTimeField | yes | indexed |
| `completed_at` | DateTimeField | yes |  |
| `duration_ms` | PositiveIntegerField | no |  |
| `error_message` | TextField | no |  |
| `notes` | TextField | no |  |
| `is_verified` | BooleanField | no | indexed |
| `verified_at` | DateTimeField | yes |  |

Composite indexes: (-created_at), (status, -created_at), (backup_type, -created_at), (scope, status)

#### RestoreRecord

*restore* — table `backups_restorerecord`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `backup` | ForeignKey | no | indexed, → backups.BackupRecord, PROTECT |
| `status` | CharField | no | indexed, 6 choices |
| `safety_backup` | ForeignKey | yes | indexed, → backups.BackupRecord, SET_NULL |
| `started_at` | DateTimeField | yes | indexed |
| `completed_at` | DateTimeField | yes |  |
| `duration_ms` | PositiveIntegerField | no |  |
| `error_message` | TextField | no |  |
| `confirmed_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `confirmation_text` | CharField | no |  |
| `pre_restore_checks` | JSONField | no |  |
| `notes` | TextField | no |  |

Composite indexes: (-created_at), (status, -created_at)


### `bookings`

#### Booking

*booking* — table `bookings_booking`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `booking_code` | CharField | no | unique, indexed |
| `booking_type` | CharField | no | indexed, 4 choices |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `student` | ForeignKey | yes | indexed, → students.Student, SET_NULL |
| `lesson` | ForeignKey | yes | indexed, → lessons.Lesson, SET_NULL |
| `surf_camp` | ForeignKey | yes | indexed, → surf_camps.SurfCamp, SET_NULL |
| `status` | CharField | no | indexed, 7 choices |
| `payment_status` | CharField | no | indexed, 5 choices |
| `participants` | PositiveSmallIntegerField | no |  |
| `unit_price` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `total_amount` | DecimalField | no | 12,2 |
| `paid_amount` | DecimalField | no | 12,2 |
| `source` | CharField | no | indexed, 8 choices |
| `booked_at` | DateTimeField | no | indexed |
| `confirmed_at` | DateTimeField | yes |  |
| `cancelled_at` | DateTimeField | yes |  |
| `cancellation_reason` | TextField | no |  |
| `cancellation_fee` | DecimalField | no | 12,2 |
| `special_requests` | TextField | no |  |
| `internal_notes` | TextField | no |  |
| `reminder_sent` | BooleanField | no |  |
| `reminder_sent_at` | DateTimeField | yes |  |

Composite indexes: (status, booked_at), (customer, status), (lesson, status), (payment_status, status)

#### WaitlistEntry

*waiting-list entry* — table `bookings_waitlistentry`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `lesson` | ForeignKey | yes | indexed, → lessons.Lesson, SET_NULL |
| `surf_camp` | ForeignKey | yes | indexed, → surf_camps.SurfCamp, SET_NULL |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `student` | ForeignKey | yes | indexed, → students.Student, SET_NULL |
| `requested_at` | DateTimeField | no | indexed |
| `participants` | PositiveSmallIntegerField | no |  |
| `position` | PositiveIntegerField | no | indexed |
| `is_notified` | BooleanField | no |  |
| `notified_at` | DateTimeField | yes |  |
| `is_converted` | BooleanField | no | indexed |
| `converted_booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `note` | CharField | no |  |

Composite indexes: (lesson, is_converted, position), (surf_camp, is_converted, position)


### `core`

#### Document

*document* — table `core_document`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `content_type` | ForeignKey | no | indexed, → contenttypes.ContentType, CASCADE |
| `object_id` | PositiveIntegerField | no |  |
| `title` | CharField | no |  |
| `category` | CharField | no | 10 choices |
| `file` | FileField | no |  |
| `content_type_hint` | CharField | no |  |
| `size_bytes` | PositiveBigIntegerField | no |  |
| `expires_on` | DateField | yes |  |
| `is_confidential` | BooleanField | no |  |

Composite indexes: (content_type, object_id)

#### Note

*note* — table `core_note`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `content_type` | ForeignKey | no | indexed, → contenttypes.ContentType, CASCADE |
| `object_id` | PositiveIntegerField | no |  |
| `body` | TextField | no |  |
| `is_pinned` | BooleanField | no |  |
| `is_internal` | BooleanField | no |  |

Composite indexes: (content_type, object_id)

#### SystemSetting

*system setting* — table `core_systemsetting`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `key` | CharField | no | unique |
| `value` | TextField | no |  |
| `value_type` | CharField | no | 5 choices |
| `group` | CharField | no | indexed |
| `label_en` | CharField | no |  |
| `label_tr` | CharField | no |  |
| `is_secret` | BooleanField | no |  |

#### Tag

*tag* — table `core_tag`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `name` | CharField | no | unique |
| `slug` | SlugField | no | unique, indexed |
| `color` | CharField | no |  |
| `description` | CharField | no |  |


### `crm`

#### Campaign

*campaign* — table `crm_campaign`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no |  |
| `code` | CharField | no | unique |
| `channel` | CharField | no | indexed, 6 choices |
| `start_date` | DateField | no | indexed |
| `end_date` | DateField | no | indexed |
| `budget` | DecimalField | no | 12,2 |
| `actual_spend` | DecimalField | no | 12,2 |
| `target_segment` | ForeignKey | yes | indexed, → crm.Segment, SET_NULL |
| `message_subject` | CharField | no |  |
| `message_body` | TextField | no |  |
| `status` | CharField | no | indexed, 5 choices |
| `sent_count` | PositiveIntegerField | no |  |
| `opened_count` | PositiveIntegerField | no |  |
| `converted_count` | PositiveIntegerField | no |  |
| `revenue_attributed` | DecimalField | no | 12,2 |

Composite indexes: (status, -start_date), (channel, status)

Constraints: crm_campaign_end_after_start, crm_campaign_money_not_negative

#### Interaction

*interaction* — table `crm_interaction`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `kind` | CharField | no | indexed, 8 choices |
| `direction` | CharField | no | indexed, 2 choices |
| `subject` | CharField | no |  |
| `body` | TextField | no |  |
| `customer` | ForeignKey | yes | indexed, → customers.Customer, PROTECT |
| `lead` | ForeignKey | yes | indexed, → crm.Lead, CASCADE |
| `occurred_at` | DateTimeField | no | indexed |
| `duration_minutes` | PositiveIntegerField | yes |  |
| `handled_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `follow_up_required` | BooleanField | no | indexed |
| `follow_up_at` | DateTimeField | yes | indexed |
| `sentiment` | CharField | no | 3 choices |

Composite indexes: (customer, -occurred_at), (lead, -occurred_at), (follow_up_required, follow_up_at), (kind, -occurred_at)

#### Lead

*lead* — table `crm_lead`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `first_name` | CharField | no |  |
| `last_name` | CharField | no |  |
| `email` | CharField | no | indexed |
| `phone` | CharField | no | indexed |
| `source` | CharField | no | indexed, 8 choices |
| `interest` | TextField | no |  |
| `status` | CharField | no | indexed, 6 choices |
| `assigned_to` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `expected_value` | DecimalField | no | 12,2 |
| `probability` | DecimalField | no | 5,2 |
| `next_action` | CharField | no |  |
| `next_action_at` | DateTimeField | yes | indexed |
| `converted_customer` | ForeignKey | yes | indexed, → customers.Customer, SET_NULL |
| `converted_at` | DateTimeField | yes |  |
| `lost_reason` | CharField | no |  |

Composite indexes: (status, -created_at), (assigned_to, next_action_at), (source, status)

Constraints: crm_lead_probability_range, crm_lead_expected_value_not_negative

#### Segment

*segment* — table `crm_segment`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no | unique |
| `description` | TextField | no |  |
| `criteria` | JSONField | no |  |
| `is_dynamic` | BooleanField | no |  |
| `cached_count` | PositiveIntegerField | no |  |
| `last_calculated_at` | DateTimeField | yes |  |

Composite indexes: (is_dynamic, name)


### `customers`

#### Customer

*customer* — table `customers_customer`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `address_line1` | CharField | no |  |
| `address_line2` | CharField | no |  |
| `city` | CharField | no |  |
| `state` | CharField | no |  |
| `postal_code` | CharField | no |  |
| `country` | CharField | no |  |
| `customer_code` | CharField | no | unique, indexed |
| `user` | OneToOneField | yes | unique, indexed, → accounts.User, SET_NULL |
| `first_name` | CharField | no |  |
| `last_name` | CharField | no |  |
| `email` | CharField | no | indexed |
| `phone` | CharField | no | indexed |
| `photo` | FileField | yes |  |
| `birth_date` | DateField | yes |  |
| `gender` | CharField | no | 4 choices |
| `nationality` | CharField | no |  |
| `preferred_language` | CharField | no | 7 choices |
| `emergency_contact_name` | CharField | no |  |
| `emergency_contact_phone` | CharField | no |  |
| `emergency_contact_relation` | CharField | no |  |
| `source` | CharField | no | indexed, 8 choices |
| `marketing_consent` | BooleanField | no |  |
| `marketing_consent_at` | DateTimeField | yes |  |
| `is_active` | BooleanField | no | indexed |
| `first_visit_date` | DateField | yes |  |
| `last_visit_date` | DateField | yes | indexed |
| `lifetime_value` | DecimalField | no | 12,2 |
| `total_bookings` | PositiveIntegerField | no |  |
| `notes` | TextField | no |  |
| `tags` | ManyToManyField | no |  |

Composite indexes: (last_name, first_name), (is_active, source), (email, is_deleted), (phone, last_name), (-last_visit_date)

#### CustomerTag

*customer tag* — table `customers_customertag`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `customer` | ForeignKey | no | indexed, → customers.Customer, CASCADE |
| `tag` | ForeignKey | no | indexed, → core.Tag, CASCADE |
| `added_at` | DateTimeField | no |  |
| `added_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |

Composite indexes: (tag, customer)

Constraints: customers_customertag_unique


### `equipment`

#### Equipment

*equipment item* — table `equipment_equipment`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `asset_code` | CharField | no | unique |
| `category` | ForeignKey | no | indexed, → equipment.EquipmentCategory, PROTECT |
| `name` | CharField | no |  |
| `brand` | CharField | no |  |
| `model` | CharField | no |  |
| `serial_number` | CharField | no | indexed |
| `size_label` | CharField | no |  |
| `length_cm` | DecimalField | yes | 6,1 |
| `width_cm` | DecimalField | yes | 6,1 |
| `thickness_cm` | DecimalField | yes | 6,1 |
| `volume_litres` | DecimalField | yes | 6,2 |
| `wetsuit_thickness` | CharField | no |  |
| `suitable_min_level` | CharField | no | indexed, 6 choices |
| `suitable_max_level` | CharField | no | 6 choices |
| `min_rider_weight_kg` | DecimalField | yes | 5,1 |
| `max_rider_weight_kg` | DecimalField | yes | 5,1 |
| `purchase_date` | DateField | yes |  |
| `purchase_price` | DecimalField | no | 12,2 |
| `current_value` | DecimalField | no | 12,2 |
| `supplier` | CharField | no |  |
| `status` | CharField | no | indexed, 8 choices |
| `condition` | CharField | no | indexed, 6 choices |
| `storage_location` | CharField | no |  |
| `is_rentable` | BooleanField | no |  |
| `is_lesson_stock` | BooleanField | no |  |
| `rental_price_hourly` | DecimalField | no | 12,2 |
| `rental_price_daily` | DecimalField | no | 12,2 |
| `rental_price_weekly` | DecimalField | no | 12,2 |
| `deposit_amount` | DecimalField | no | 12,2 |
| `total_rentals` | PositiveIntegerField | no |  |
| `total_rental_hours` | DecimalField | no | 10,2 |
| `last_maintenance_date` | DateField | yes |  |
| `next_maintenance_date` | DateField | yes | indexed |
| `notes` | TextField | no |  |
| `retired_at` | DateTimeField | yes |  |
| `retired_reason` | CharField | no |  |

Composite indexes: (status, category), (is_rentable, status), (next_maintenance_date)

#### EquipmentCategory

*equipment category* — table `equipment_equipmentcategory`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `code` | SlugField | no | unique, indexed |
| `name` | CharField | no |  |
| `parent` | ForeignKey | yes | indexed, → equipment.EquipmentCategory, PROTECT |
| `icon` | CharField | no |  |
| `sort_order` | PositiveIntegerField | no |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (parent, sort_order)

#### EquipmentPhoto

*equipment photo* — table `equipment_equipmentphoto`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `equipment` | ForeignKey | no | indexed, → equipment.Equipment, CASCADE |
| `image` | FileField | no |  |
| `caption` | CharField | no |  |
| `is_primary` | BooleanField | no |  |
| `taken_at` | DateTimeField | yes |  |

Composite indexes: (equipment, -is_primary)


### `finance`

#### CommissionRecord

*commission record* — table `finance_commissionrecord`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, PROTECT |
| `lesson` | ForeignKey | yes | indexed, → lessons.Lesson, SET_NULL |
| `period_start` | DateField | no | indexed |
| `period_end` | DateField | no | indexed |
| `base_amount` | DecimalField | no | 12,2 |
| `commission_percent` | DecimalField | no | 5,2 |
| `commission_amount` | DecimalField | no | 12,2 |
| `status` | CharField | no | indexed, 4 choices |
| `paid_at` | DateTimeField | yes | indexed |
| `notes` | TextField | no |  |

Composite indexes: (instructor, status), (status, period_end)

#### CustomerPackage

*customer package* — table `finance_customerpackage`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `package` | ForeignKey | no | indexed, → finance.PricePackage, PROTECT |
| `purchased_on` | DateField | no | indexed |
| `expires_on` | DateField | no | indexed |
| `lessons_total` | PositiveSmallIntegerField | no |  |
| `lessons_used` | PositiveSmallIntegerField | no |  |
| `amount_paid` | DecimalField | no | 12,2 |
| `status` | CharField | no | indexed, 4 choices |

Composite indexes: (customer, status), (status, expires_on)

#### Expense

*expense* — table `finance_expense`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `expense_code` | CharField | no | unique, indexed |
| `category` | ForeignKey | no | indexed, → finance.ExpenseCategory, PROTECT |
| `description` | CharField | no |  |
| `amount` | DecimalField | no | 12,2 |
| `tax_amount` | DecimalField | no | 12,2 |
| `spent_on` | DateField | no | indexed |
| `paid_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `supplier` | CharField | no |  |
| `invoice_reference` | CharField | no |  |
| `receipt` | FileField | yes |  |
| `is_recurring` | BooleanField | no | indexed |
| `recurrence_months` | PositiveSmallIntegerField | yes |  |
| `equipment` | ForeignKey | yes | indexed, → equipment.Equipment, SET_NULL |

Composite indexes: (category, spent_on), (spent_on, is_recurring)

#### ExpenseCategory

*expense category* — table `finance_expensecategory`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `code` | CharField | no | unique, indexed |
| `name` | CharField | no |  |
| `is_active` | BooleanField | no | indexed |
| `sort_order` | PositiveSmallIntegerField | no |  |

#### Invoice

*invoice* — table `finance_invoice`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `invoice_number` | CharField | no | unique, indexed |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `rental` | ForeignKey | yes | indexed, → rentals.Rental, SET_NULL |
| `issue_date` | DateField | no | indexed |
| `due_date` | DateField | no | indexed |
| `status` | CharField | no | indexed, 7 choices |
| `subtotal` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `tax_rate` | DecimalField | no | 5,2 |
| `tax_amount` | DecimalField | no | 12,2 |
| `total_amount` | DecimalField | no | 12,2 |
| `paid_amount` | DecimalField | no | 12,2 |
| `currency` | CharField | no |  |
| `notes` | TextField | no |  |
| `terms` | TextField | no |  |

Composite indexes: (status, due_date), (customer, status), (issue_date, status)

#### InvoiceLine

*invoice line* — table `finance_invoiceline`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `invoice` | ForeignKey | no | indexed, → finance.Invoice, CASCADE |
| `description` | CharField | no |  |
| `quantity` | DecimalField | no | 8,2 |
| `unit_price` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `line_total` | DecimalField | no | 12,2 |
| `sort_order` | PositiveSmallIntegerField | no |  |

Composite indexes: (invoice, sort_order)

#### Payment

*payment* — table `finance_payment`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `payment_code` | CharField | no | unique, indexed |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `invoice` | ForeignKey | yes | indexed, → finance.Invoice, SET_NULL |
| `booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `rental` | ForeignKey | yes | indexed, → rentals.Rental, SET_NULL |
| `amount` | DecimalField | no | 12,2 |
| `method` | CharField | no | indexed, 7 choices |
| `status` | CharField | no | indexed, 5 choices |
| `category` | CharField | no | indexed, 7 choices |
| `paid_at` | DateTimeField | no | indexed |
| `reference` | CharField | no |  |
| `received_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `is_refund` | BooleanField | no | indexed |
| `refunded_payment` | ForeignKey | yes | indexed, → finance.Payment, SET_NULL |
| `refund_reason` | TextField | no |  |
| `notes` | TextField | no |  |

Composite indexes: (category, paid_at), (customer, -paid_at), (method, paid_at), (is_refund, paid_at)

#### PricePackage

*price package* — table `finance_pricepackage`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no |  |
| `code` | CharField | no | unique, indexed |
| `description` | TextField | no |  |
| `lesson_type` | ForeignKey | yes | indexed, → lessons.LessonType, SET_NULL |
| `lesson_count` | PositiveSmallIntegerField | no |  |
| `price` | DecimalField | no | 12,2 |
| `validity_days` | PositiveSmallIntegerField | no |  |
| `is_active` | BooleanField | no | indexed |
| `sort_order` | PositiveSmallIntegerField | no |  |


### `help_center`

#### HelpArticle

*help article* — table `help_center_helparticle`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `category` | ForeignKey | no | indexed, → help_center.HelpCategory, PROTECT |
| `slug` | SlugField | no | unique, indexed |
| `title_en` | CharField | no |  |
| `title_tr` | CharField | no |  |
| `body_en` | TextField | no |  |
| `body_tr` | TextField | no |  |
| `keywords` | CharField | no | indexed |
| `sort_order` | PositiveIntegerField | no | indexed |
| `is_published` | BooleanField | no | indexed |
| `view_count` | PositiveIntegerField | no |  |
| `helpful_count` | PositiveIntegerField | no |  |
| `not_helpful_count` | PositiveIntegerField | no |  |
| `related_module` | CharField | no | indexed, 28 choices |

Composite indexes: (category, sort_order), (is_published, sort_order), (related_module, is_published)

#### HelpCategory

*help category* — table `help_center_helpcategory`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `code` | SlugField | no | unique, indexed |
| `name_en` | CharField | no |  |
| `name_tr` | CharField | no |  |
| `icon` | CharField | no |  |
| `sort_order` | PositiveIntegerField | no | indexed |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (is_active, sort_order)


### `instructors`

#### AvailabilitySlot

*availability slot* — table `instructors_availabilityslot`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, CASCADE |
| `weekday` | PositiveSmallIntegerField | no | indexed, 7 choices |
| `start_time` | TimeField | no |  |
| `end_time` | TimeField | no |  |
| `is_active` | BooleanField | no | indexed |
| `valid_from` | DateField | yes |  |
| `valid_until` | DateField | yes |  |

Composite indexes: (instructor, weekday, is_active)

Constraints: uniq_availability_slot_start

#### Certification

*certification* — table `instructors_certification`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, CASCADE |
| `kind` | CharField | no | indexed, 9 choices |
| `name` | CharField | no |  |
| `issuing_body` | CharField | no |  |
| `certificate_number` | CharField | no |  |
| `issued_on` | DateField | no |  |
| `expires_on` | DateField | yes | indexed |
| `document` | FileField | yes |  |
| `is_verified` | BooleanField | no | indexed |
| `verified_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `verified_at` | DateTimeField | yes |  |

Composite indexes: (instructor, kind), (expires_on, is_verified)

Constraints: uniq_certificate_number_per_instructor_kind

#### Instructor

*instructor* — table `instructors_instructor`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `user` | OneToOneField | no | unique, indexed, → accounts.User, PROTECT |
| `instructor_code` | CharField | no | unique, indexed |
| `bio` | TextField | no |  |
| `photo` | FileField | yes |  |
| `specialties` | JSONField | no |  |
| `languages` | JSONField | no |  |
| `max_level_taught` | CharField | no | indexed, 6 choices |
| `max_students_per_lesson` | PositiveSmallIntegerField | no |  |
| `hourly_rate` | DecimalField | no | 12,2 |
| `commission_percent` | DecimalField | no | 5,2 |
| `hire_date` | DateField | yes | indexed |
| `is_active` | BooleanField | no | indexed |
| `is_available_for_booking` | BooleanField | no | indexed |
| `rating_average` | DecimalField | no | 3,2 |
| `rating_count` | PositiveIntegerField | no |  |
| `total_lessons_taught` | PositiveIntegerField | no |  |
| `emergency_contact_name` | CharField | no |  |
| `emergency_contact_phone` | CharField | no |  |

Composite indexes: (is_active, is_available_for_booking), (max_level_taught, is_active)

#### PerformanceReview

*performance review* — table `instructors_performancereview`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, CASCADE |
| `period_start` | DateField | no |  |
| `period_end` | DateField | no |  |
| `reviewer` | ForeignKey | no | indexed, → accounts.User, PROTECT |
| `teaching_quality` | PositiveSmallIntegerField | no | 5 choices |
| `punctuality` | PositiveSmallIntegerField | no | 5 choices |
| `safety` | PositiveSmallIntegerField | no | 5 choices |
| `communication` | PositiveSmallIntegerField | no | 5 choices |
| `teamwork` | PositiveSmallIntegerField | no | 5 choices |
| `strengths` | TextField | no |  |
| `improvements` | TextField | no |  |
| `goals` | TextField | no |  |
| `overall_score` | DecimalField | no | 3,2 |

Composite indexes: (instructor, period_end)

#### TimeOff

*time off* — table `instructors_timeoff`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, CASCADE |
| `start_date` | DateField | no | indexed |
| `end_date` | DateField | no | indexed |
| `reason` | CharField | no | indexed, 5 choices |
| `note` | TextField | no |  |
| `is_approved` | BooleanField | no | indexed |
| `approved_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `approved_at` | DateTimeField | yes |  |

Composite indexes: (instructor, start_date, end_date), (is_approved, start_date)


### `lessons`

#### Lesson

*lesson* — table `lessons_lesson`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `lesson_code` | CharField | no | unique |
| `lesson_type` | ForeignKey | no | indexed, → lessons.LessonType, PROTECT |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `date` | DateField | no | indexed |
| `start_time` | TimeField | no |  |
| `end_time` | TimeField | no |  |
| `instructor` | ForeignKey | no | indexed, → instructors.Instructor, PROTECT |
| `capacity` | PositiveSmallIntegerField | no |  |
| `status` | CharField | no | indexed, 6 choices |
| `price_override` | DecimalField | yes | 12,2 |
| `notes` | TextField | no |  |
| `internal_notes` | TextField | no |  |
| `conditions_snapshot` | JSONField | no |  |
| `safety_briefing_done` | BooleanField | no |  |
| `safety_checked_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `safety_checked_at` | DateTimeField | yes |  |
| `cancellation_reason` | TextField | no |  |
| `cancelled_at` | DateTimeField | yes |  |
| `assistant_instructors` | ManyToManyField | no |  |

Composite indexes: (date, status), (instructor, date)

#### LessonAttendance

*lesson attendance* — table `lessons_lessonattendance`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `lesson` | ForeignKey | no | indexed, → lessons.Lesson, CASCADE |
| `student` | ForeignKey | no | indexed, → students.Student, PROTECT |
| `booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `status` | CharField | no | indexed, 5 choices |
| `checked_in_at` | DateTimeField | yes |  |
| `rating` | PositiveSmallIntegerField | yes |  |
| `student_feedback` | TextField | no |  |
| `instructor_notes` | TextField | no |  |
| `assigned_board` | ForeignKey | yes | indexed, → equipment.Equipment, SET_NULL |
| `assigned_wetsuit` | ForeignKey | yes | indexed, → equipment.Equipment, SET_NULL |

Composite indexes: (lesson, status), (student, status)

Constraints: lessons_attendance_unique_student

#### LessonType

*lesson type* — table `lessons_lessontype`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `code` | CharField | no | unique |
| `name` | CharField | no |  |
| `description` | TextField | no |  |
| `category` | CharField | no | indexed, 10 choices |
| `min_level` | CharField | no | 6 choices |
| `max_level` | CharField | no | 6 choices |
| `min_age` | PositiveSmallIntegerField | yes |  |
| `max_age` | PositiveSmallIntegerField | yes |  |
| `duration_minutes` | PositiveSmallIntegerField | no |  |
| `max_students` | PositiveSmallIntegerField | no |  |
| `min_students` | PositiveSmallIntegerField | no |  |
| `base_price` | DecimalField | no | 12,2 |
| `price_per_extra_student` | DecimalField | no | 12,2 |
| `requires_board` | BooleanField | no |  |
| `requires_wetsuit` | BooleanField | no |  |
| `requires_leash` | BooleanField | no |  |
| `colour` | CharField | no |  |
| `is_active` | BooleanField | no | indexed |
| `sort_order` | PositiveSmallIntegerField | no |  |

Composite indexes: (is_active, sort_order)


### `locations`

#### SpotHazard

*spot hazard* — table `locations_spothazard`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, CASCADE |
| `name` | CharField | no |  |
| `severity` | CharField | no | indexed, 4 choices |
| `description` | TextField | no |  |
| `is_active` | BooleanField | no | indexed |
| `applies_from_tide` | CharField | no | 5 choices |
| `applies_to_tide` | CharField | no | 5 choices |

Composite indexes: (spot, is_active)

#### SurfSpot

*surf spot* — table `locations_surfspot`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no | indexed |
| `slug` | SlugField | no | unique, indexed |
| `code` | CharField | no | unique |
| `description` | TextField | no |  |
| `latitude` | FloatField | no |  |
| `longitude` | FloatField | no |  |
| `altitude` | FloatField | yes |  |
| `beach_facing_deg` | FloatField | no |  |
| `break_type` | CharField | no | indexed, 4 choices |
| `bottom_type` | CharField | no | indexed, 4 choices |
| `min_level` | CharField | no | indexed, 6 choices |
| `max_level` | CharField | no | indexed, 6 choices |
| `ideal_tide` | CharField | no | 5 choices |
| `ideal_wind` | CharField | no | 6 choices |
| `ideal_swell_direction_deg` | FloatField | yes |  |
| `capacity` | PositiveIntegerField | no |  |
| `is_active` | BooleanField | no | indexed |
| `is_primary` | BooleanField | no |  |
| `parking_info` | TextField | no |  |
| `access_notes` | TextField | no |  |
| `photo` | FileField | yes |  |
| `lifeguard_on_duty` | BooleanField | no | indexed |
| `nearest_hospital` | CharField | no |  |
| `nearest_hospital_phone` | CharField | no |  |
| `emergency_notes` | TextField | no |  |

Composite indexes: (is_active, is_primary), (min_level, max_level)

Constraints: loc_spot_single_primary


### `maintenance`

#### MaintenanceRecord

*maintenance record* — table `maintenance_maintenancerecord`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `record_code` | CharField | no | unique, indexed |
| `equipment` | ForeignKey | no | indexed, → equipment.Equipment, PROTECT |
| `damage_type` | CharField | no | indexed, 11 choices |
| `severity` | CharField | no | indexed, 4 choices |
| `status` | CharField | no | indexed, 6 choices |
| `reported_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `reported_at` | DateTimeField | no | indexed |
| `assigned_to` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `started_at` | DateTimeField | yes |  |
| `completed_at` | DateTimeField | yes |  |
| `description` | TextField | no |  |
| `diagnosis` | TextField | no |  |
| `resolution` | TextField | no |  |
| `parts_used` | TextField | no |  |
| `labour_hours` | DecimalField | no | 5,2 |
| `parts_cost` | DecimalField | no | 12,2 |
| `labour_cost` | DecimalField | no | 12,2 |
| `total_cost` | DecimalField | no | 12,2 |
| `photo_before` | FileField | yes |  |
| `photo_after` | FileField | yes |  |
| `rental_item` | ForeignKey | yes | indexed, → rentals.RentalItem, SET_NULL |
| `made_unusable` | BooleanField | no |  |

Composite indexes: (status, severity), (equipment, -reported_at), (-reported_at)

#### MaintenanceSchedule

*maintenance schedule* — table `maintenance_maintenanceschedule`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `equipment` | OneToOneField | no | unique, indexed, → equipment.Equipment, CASCADE |
| `interval_days` | PositiveIntegerField | no |  |
| `last_performed_on` | DateField | yes |  |
| `next_due_on` | DateField | yes | indexed |
| `check_items` | JSONField | no |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (is_active, next_due_on)


### `notifications`

#### Notification

*notification* — table `notifications_notification`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `recipient` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `category` | CharField | no | indexed, 12 choices |
| `level` | CharField | no | indexed, 4 choices |
| `title` | CharField | no |  |
| `body` | TextField | no |  |
| `link_url` | CharField | no |  |
| `is_read` | BooleanField | no | indexed |
| `read_at` | DateTimeField | yes |  |
| `is_emailed` | BooleanField | no |  |
| `emailed_at` | DateTimeField | yes |  |
| `related_object_type` | CharField | no |  |
| `related_object_id` | PositiveIntegerField | yes |  |

Composite indexes: (recipient, is_read, -created_at), (category, -created_at), (related_object_type, related_object_id)

#### NotificationPreference

*notification preference* — table `notifications_notificationpreference`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `user` | OneToOneField | no | unique, indexed, → accounts.User, CASCADE |
| `in_app_enabled` | BooleanField | no |  |
| `email_enabled` | BooleanField | no |  |
| `categories_muted` | JSONField | no |  |
| `quiet_hours_start` | TimeField | yes |  |
| `quiet_hours_end` | TimeField | yes |  |

#### NotificationTemplate

*notification template* — table `notifications_notificationtemplate`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `code` | SlugField | no | unique, indexed |
| `category` | CharField | no | indexed, 12 choices |
| `level` | CharField | no | 4 choices |
| `title_en` | CharField | no |  |
| `title_tr` | CharField | no |  |
| `body_en` | TextField | no |  |
| `body_tr` | TextField | no |  |
| `is_active` | BooleanField | no | indexed |


### `onboarding`

#### OnboardingState

*onboarding state* — table `onboarding_onboardingstate`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `is_completed` | BooleanField | no | indexed |
| `is_dismissed` | BooleanField | no |  |
| `current_step` | PositiveIntegerField | no |  |
| `completed_steps` | JSONField | no |  |
| `school_name` | CharField | no |  |
| `contact_email` | CharField | no |  |
| `contact_phone` | CharField | no |  |
| `address` | CharField | no |  |
| `default_language` | CharField | no |  |
| `currency` | CharField | no | 4 choices |
| `timezone` | CharField | no |  |
| `primary_spot_name` | CharField | no |  |
| `latitude` | FloatField | yes |  |
| `longitude` | FloatField | yes |  |
| `beach_facing_deg` | FloatField | yes |  |
| `staff_invited` | BooleanField | no |  |
| `ai_configured` | BooleanField | no |  |
| `backup_configured` | BooleanField | no |  |
| `records_created` | BooleanField | no |  |
| `started_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `completed_at` | DateTimeField | yes |  |


### `pos`

#### Product

*product* — table `pos_product`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `sku` | CharField | no | unique |
| `barcode` | CharField | no | indexed |
| `name` | CharField | no | indexed |
| `description` | TextField | no |  |
| `category` | ForeignKey | no | indexed, → pos.ProductCategory, PROTECT |
| `cost_price` | DecimalField | no | 12,2 |
| `sale_price` | DecimalField | no | 12,2 |
| `tax_rate` | DecimalField | no | 5,2 |
| `stock_quantity` | IntegerField | no | indexed |
| `low_stock_threshold` | PositiveIntegerField | no |  |
| `track_stock` | BooleanField | no |  |
| `unit` | CharField | no | indexed, 5 choices |
| `photo` | FileField | yes |  |
| `supplier` | CharField | no |  |
| `is_active` | BooleanField | no | indexed |
| `sort_order` | PositiveIntegerField | no |  |

Composite indexes: (is_active, category), (is_active, sort_order), (track_stock, stock_quantity)

Constraints: pos_product_unique_barcode

#### ProductCategory

*product category* — table `pos_productcategory`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `code` | SlugField | no | unique, indexed |
| `name` | CharField | no |  |
| `parent` | ForeignKey | yes | indexed, → pos.ProductCategory, PROTECT |
| `icon` | CharField | no |  |
| `sort_order` | PositiveIntegerField | no |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (parent, sort_order), (is_active, sort_order)

#### Sale

*sale* — table `pos_sale`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `sale_number` | CharField | no | unique |
| `sold_at` | DateTimeField | no | indexed |
| `customer` | ForeignKey | yes | indexed, → customers.Customer, SET_NULL |
| `cashier` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `subtotal` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `discount_percent` | DecimalField | no | 5,2 |
| `tax_amount` | DecimalField | no | 12,2 |
| `total_amount` | DecimalField | no | 12,2 |
| `paid_amount` | DecimalField | no | 12,2 |
| `change_given` | DecimalField | no | 12,2 |
| `payment_method` | CharField | no | indexed, 7 choices |
| `status` | CharField | no | indexed, 4 choices |
| `note` | TextField | no |  |
| `voided_at` | DateTimeField | yes |  |
| `void_reason` | TextField | no |  |

Composite indexes: (status, sold_at), (cashier, sold_at), (customer, sold_at)

#### SaleItem

*sale item* — table `pos_saleitem`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `sale` | ForeignKey | no | indexed, → pos.Sale, CASCADE |
| `product` | ForeignKey | no | indexed, → pos.Product, PROTECT |
| `quantity` | DecimalField | no | 8,2 |
| `unit_price` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `tax_amount` | DecimalField | no | 12,2 |
| `line_total` | DecimalField | no | 12,2 |

Composite indexes: (sale, product), (product)

#### StockMovement

*stock movement* — table `pos_stockmovement`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `product` | ForeignKey | no | indexed, → pos.Product, PROTECT |
| `movement_type` | CharField | no | indexed, 7 choices |
| `quantity` | DecimalField | no | 8,2 |
| `balance_after` | DecimalField | no | 10,2 |
| `reference` | CharField | no | indexed |
| `note` | TextField | no |  |
| `sale` | ForeignKey | yes | indexed, → pos.Sale, SET_NULL |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |

Composite indexes: (product, -created_at), (movement_type, -created_at), (sale)


### `rentals`

#### Rental

*rental* — table `rentals_rental`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `rental_code` | CharField | no | unique, indexed |
| `customer` | ForeignKey | no | indexed, → customers.Customer, PROTECT |
| `student` | ForeignKey | yes | indexed, → students.Student, SET_NULL |
| `booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `status` | CharField | no | indexed, 6 choices |
| `period_type` | CharField | no | indexed, 3 choices |
| `start_at` | DateTimeField | no | indexed |
| `expected_return_at` | DateTimeField | no | indexed |
| `returned_at` | DateTimeField | yes | indexed |
| `deposit_amount` | DecimalField | no | 12,2 |
| `deposit_returned` | DecimalField | no | 12,2 |
| `deposit_status` | CharField | no | indexed, 3 choices |
| `subtotal` | DecimalField | no | 12,2 |
| `discount_amount` | DecimalField | no | 12,2 |
| `late_fee` | DecimalField | no | 12,2 |
| `damage_fee` | DecimalField | no | 12,2 |
| `total_amount` | DecimalField | no | 12,2 |
| `paid_amount` | DecimalField | no | 12,2 |
| `payment_status` | CharField | no | indexed, 5 choices |
| `id_document_held` | BooleanField | no |  |
| `notes` | TextField | no |  |
| `checked_out_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `checked_in_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |

Composite indexes: (status, expected_return_at), (customer, -start_at), (payment_status, -start_at)

#### RentalItem

*rental item* — table `rentals_rentalitem`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `rental` | ForeignKey | no | indexed, → rentals.Rental, CASCADE |
| `equipment` | ForeignKey | no | indexed, → equipment.Equipment, PROTECT |
| `unit_price` | DecimalField | no | 12,2 |
| `quantity` | PositiveIntegerField | no |  |
| `line_total` | DecimalField | no | 12,2 |
| `condition_out` | CharField | no | 6 choices |
| `condition_in` | CharField | yes | 6 choices |
| `damage_reported` | BooleanField | no | indexed |
| `damage_type` | CharField | no | 11 choices |
| `damage_notes` | TextField | no |  |
| `damage_charge` | DecimalField | no | 12,2 |
| `returned_at` | DateTimeField | yes | indexed |

Composite indexes: (equipment, returned_at)

Constraints: rentals_unique_equipment_per_rental


### `reporting`

#### GeneratedReport

*generated report* — table `reporting_generatedreport`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `definition` | ForeignKey | yes | indexed, → reporting.ReportDefinition, SET_NULL |
| `report_key` | CharField | no | indexed |
| `title` | CharField | no |  |
| `format` | CharField | no | 3 choices |
| `filters_used` | JSONField | no |  |
| `file` | FileField | no |  |
| `file_size_bytes` | PositiveBigIntegerField | no |  |
| `row_count` | PositiveIntegerField | no |  |
| `generated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `generation_ms` | PositiveIntegerField | no |  |
| `status` | CharField | no | indexed, 3 choices |
| `error_message` | TextField | no |  |

Composite indexes: (-created_at), (report_key, -created_at), (status, -created_at)

#### ReportDefinition

*report definition* — table `reporting_reportdefinition`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no | indexed |
| `code` | SlugField | no | unique, indexed |
| `report_key` | CharField | no | indexed |
| `description` | TextField | no |  |
| `default_format` | CharField | no | 3 choices |
| `default_filters` | JSONField | no |  |
| `required_capability` | CharField | no |  |
| `is_scheduled` | BooleanField | no | indexed |
| `schedule_cron` | CharField | no |  |
| `recipients` | JSONField | no |  |
| `last_run_at` | DateTimeField | yes |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (report_key, is_active), (is_scheduled, is_active)


### `safety`

#### EmergencyContact

*emergency contact* — table `safety_emergencycontact`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `name` | CharField | no |  |
| `organisation` | CharField | no |  |
| `kind` | CharField | no | indexed, 8 choices |
| `phone` | CharField | no |  |
| `alternate_phone` | CharField | no |  |
| `address` | CharField | no |  |
| `notes` | TextField | no |  |
| `spot` | ForeignKey | yes | indexed, → locations.SurfSpot, CASCADE |
| `sort_order` | PositiveSmallIntegerField | no |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (is_active, sort_order), (spot, is_active)

#### EquipmentSafetyCheck

*equipment safety check* — table `safety_equipmentsafetycheck`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `equipment` | ForeignKey | no | indexed, → equipment.Equipment, PROTECT |
| `checked_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `checked_at` | DateTimeField | no | indexed |
| `passed` | BooleanField | no | indexed |
| `checklist` | JSONField | no |  |
| `issues_found` | TextField | no |  |
| `action_taken` | TextField | no |  |
| `next_check_due` | DateField | yes | indexed |

Composite indexes: (equipment, -checked_at), (passed, next_check_due)

#### EvacuationPlan

*evacuation plan* — table `safety_evacuationplan`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `title` | CharField | no |  |
| `trigger_conditions` | TextField | no |  |
| `assembly_point` | CharField | no |  |
| `steps` | JSONField | no |  |
| `responsible_role` | CharField | no | 15 choices |
| `document` | FileField | yes |  |
| `last_drill_date` | DateField | yes |  |
| `next_drill_due` | DateField | yes | indexed |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (spot, is_active), (is_active, next_drill_due)

#### LifeguardAssignment

*lifeguard assignment* — table `safety_lifeguardassignment`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `lifeguard` | ForeignKey | no | indexed, → accounts.User, PROTECT |
| `date` | DateField | no | indexed |
| `start_time` | TimeField | no |  |
| `end_time` | TimeField | no |  |
| `is_confirmed` | BooleanField | no | indexed |
| `notes` | TextField | no |  |

Composite indexes: (date, spot), (lifeguard, date)

Constraints: saf_lifeguard_shift_unique

#### SafetyIncident

*safety incident* — table `safety_safetyincident`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `incident_code` | CharField | no | unique, indexed |
| `occurred_at` | DateTimeField | no | indexed |
| `spot` | ForeignKey | yes | indexed, → locations.SurfSpot, SET_NULL |
| `lesson` | ForeignKey | yes | indexed, → lessons.Lesson, SET_NULL |
| `incident_type` | CharField | no | indexed, 10 choices |
| `severity` | CharField | no | indexed, 4 choices |
| `status` | CharField | no | indexed, 6 choices |
| `reported_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `description` | TextField | no |  |
| `immediate_action` | TextField | no |  |
| `root_cause` | TextField | no |  |
| `corrective_action` | TextField | no |  |
| `medical_attention_required` | BooleanField | no | indexed |
| `emergency_services_called` | BooleanField | no | indexed |
| `conditions_at_time` | JSONField | no |  |
| `photo` | FileField | yes |  |
| `reviewed_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `reviewed_at` | DateTimeField | yes |  |
| `follow_up_required` | BooleanField | no | indexed |
| `follow_up_due` | DateField | yes |  |
| `people_involved` | ManyToManyField | no |  |
| `staff_involved` | ManyToManyField | no |  |

Composite indexes: (status, -occurred_at), (severity, -occurred_at), (spot, -occurred_at), (follow_up_required, follow_up_due)

#### StudentRestriction

*student restriction* — table `safety_studentrestriction`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `student` | ForeignKey | no | indexed, → students.Student, PROTECT |
| `restriction_type` | CharField | no | indexed, 6 choices |
| `description` | TextField | no |  |
| `max_wave_height_m` | FloatField | yes |  |
| `max_wind_kmh` | FloatField | yes |  |
| `requires_supervision` | BooleanField | no |  |
| `cannot_surf` | BooleanField | no | indexed |
| `starts_on` | DateField | no | indexed |
| `ends_on` | DateField | yes | indexed |
| `issued_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (student, is_active), (is_active, starts_on, ends_on)

#### WeatherWarning

*weather warning* — table `safety_weatherwarning`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `spot` | ForeignKey | yes | indexed, → locations.SurfSpot, SET_NULL |
| `title` | CharField | no |  |
| `severity` | CharField | no | indexed, 4 choices |
| `source` | CharField | no | indexed, 3 choices |
| `description` | TextField | no |  |
| `starts_at` | DateTimeField | no | indexed |
| `ends_at` | DateTimeField | no |  |
| `is_active` | BooleanField | no | indexed |
| `ai_suggested` | BooleanField | no | indexed |
| `ai_rationale` | TextField | no |  |
| `acknowledged_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `acknowledged_at` | DateTimeField | yes |  |

Composite indexes: (is_active, -starts_at), (spot, is_active), (ai_suggested, is_active)


### `students`

#### SkillAssessment

*skill assessment* — table `students_skillassessment`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `student` | ForeignKey | no | indexed, → students.Student, PROTECT |
| `instructor` | ForeignKey | yes | indexed, → instructors.Instructor, SET_NULL |
| `assessed_on` | DateField | no | indexed |
| `level_before` | CharField | no | 6 choices |
| `level_after` | CharField | no | 6 choices |
| `paddling` | PositiveSmallIntegerField | no |  |
| `popup` | PositiveSmallIntegerField | no |  |
| `positioning` | PositiveSmallIntegerField | no |  |
| `wave_reading` | PositiveSmallIntegerField | no |  |
| `safety` | PositiveSmallIntegerField | no |  |
| `notes` | TextField | no |  |
| `next_focus` | CharField | no |  |

Composite indexes: (student, -assessed_on), (instructor, -assessed_on)

#### Student

*student* — table `students_student`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `customer` | OneToOneField | no | unique, indexed, → customers.Customer, PROTECT |
| `student_code` | CharField | no | unique, indexed |
| `surf_level` | CharField | no | indexed, 6 choices |
| `goals` | TextField | no |  |
| `stance` | CharField | no | 3 choices |
| `board_preference` | CharField | no | 6 choices |
| `can_swim` | BooleanField | no | indexed |
| `swim_distance_m` | PositiveSmallIntegerField | yes |  |
| `medical_conditions` | TextField | no |  |
| `medications` | TextField | no |  |
| `allergies` | TextField | no |  |
| `weight_kg` | DecimalField | yes | 5,2 |
| `height_cm` | PositiveSmallIntegerField | yes |  |
| `shoe_size` | PositiveSmallIntegerField | yes |  |
| `wetsuit_size` | CharField | no |  |
| `preferred_instructor` | ForeignKey | yes | indexed, → instructors.Instructor, SET_NULL |
| `total_lessons` | PositiveIntegerField | no |  |
| `total_hours` | DecimalField | no | 7,2 |
| `last_lesson_date` | DateField | yes | indexed |
| `joined_at` | DateField | no |  |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (surf_level, is_active), (-last_lesson_date), (preferred_instructor, is_active)


### `surf_camps`

#### CampActivity

*camp activity* — table `surf_camps_campactivity`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `camp_day` | ForeignKey | no | indexed, → surf_camps.CampDay, CASCADE |
| `start_time` | TimeField | no |  |
| `end_time` | TimeField | no |  |
| `title` | CharField | no |  |
| `activity_type` | CharField | no | indexed, 11 choices |
| `instructor` | ForeignKey | yes | indexed, → instructors.Instructor, SET_NULL |
| `lesson` | ForeignKey | yes | indexed, → lessons.Lesson, SET_NULL |
| `location` | CharField | no |  |
| `notes` | TextField | no |  |

Composite indexes: (camp_day, start_time), (activity_type)

Constraints: camp_activity_end_after_start

#### CampDay

*camp day* — table `surf_camps_campday`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `camp` | ForeignKey | no | indexed, → surf_camps.SurfCamp, CASCADE |
| `date` | DateField | no | indexed |
| `day_number` | PositiveIntegerField | no |  |
| `title` | CharField | no |  |
| `description` | TextField | no |  |
| `weather_note` | TextField | no |  |
| `spot` | ForeignKey | yes | indexed, → locations.SurfSpot, SET_NULL |

Composite indexes: (camp, date)

Constraints: unique_active_day_per_camp

#### CampParticipant

*camp participant* — table `surf_camps_campparticipant`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `camp` | ForeignKey | no | indexed, → surf_camps.SurfCamp, CASCADE |
| `student` | ForeignKey | no | indexed, → students.Student, PROTECT |
| `booking` | ForeignKey | yes | indexed, → bookings.Booking, SET_NULL |
| `room_number` | CharField | no |  |
| `room_type` | CharField | no | 3 choices |
| `roommate_preference` | CharField | no |  |
| `arrival_datetime` | DateTimeField | yes |  |
| `departure_datetime` | DateTimeField | yes |  |
| `arrival_flight` | CharField | no |  |
| `departure_flight` | CharField | no |  |
| `needs_transfer` | BooleanField | no | indexed |
| `dietary_requirements` | CharField | no |  |
| `medical_notes` | TextField | no |  |
| `t_shirt_size` | CharField | no | 6 choices |
| `amount_paid` | DecimalField | no | 12,2 |
| `deposit_paid` | BooleanField | no |  |
| `status` | CharField | no | indexed, 5 choices |
| `cancellation_reason` | CharField | no |  |

Composite indexes: (camp, status), (status, needs_transfer)

Constraints: unique_active_participant_per_camp

#### SurfCamp

*surf camp* — table `surf_camps_surfcamp`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `name` | CharField | no | indexed |
| `code` | CharField | no | unique, indexed |
| `description` | TextField | no |  |
| `photo` | FileField | yes |  |
| `start_date` | DateField | no | indexed |
| `end_date` | DateField | no | indexed |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `capacity` | PositiveIntegerField | no |  |
| `min_participants` | PositiveIntegerField | no |  |
| `min_level` | CharField | no | indexed, 6 choices |
| `max_level` | CharField | no | indexed, 6 choices |
| `price` | DecimalField | no | 12,2 |
| `deposit_amount` | DecimalField | no | 12,2 |
| `single_room_supplement` | DecimalField | no | 12,2 |
| `includes_accommodation` | BooleanField | no |  |
| `includes_meals` | BooleanField | no |  |
| `includes_transfer` | BooleanField | no |  |
| `includes_equipment` | BooleanField | no |  |
| `includes_insurance` | BooleanField | no |  |
| `accommodation_name` | CharField | no |  |
| `accommodation_address` | TextField | no |  |
| `meal_plan` | TextField | no |  |
| `transfer_pickup_point` | CharField | no |  |
| `transfer_notes` | TextField | no |  |
| `status` | CharField | no | indexed, 6 choices |
| `lead_instructor` | ForeignKey | yes | indexed, → instructors.Instructor, SET_NULL |
| `is_active` | BooleanField | no | indexed |
| `instructors` | ManyToManyField | no |  |

Composite indexes: (status, start_date), (start_date, end_date), (is_active, status)

Constraints: surf_camp_end_date_after_start_date, surf_camp_capacity_positive


### `surf_conditions`

#### ConditionForecast

*condition forecast* — table `surf_conditions_conditionforecast`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `date` | DateField | no | indexed |
| `generated_at` | DateTimeField | no |  |
| `summary` | JSONField | no |  |
| `best_window_start` | TimeField | yes |  |
| `best_window_end` | TimeField | yes |  |
| `best_level` | CharField | no | 6 choices |

Composite indexes: (spot, date)

Constraints: sc_fc_unique_spot_day

#### SurfCondition

*surf condition* — table `surf_conditions_surfcondition`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `spot` | ForeignKey | no | indexed, → locations.SurfSpot, PROTECT |
| `recorded_at` | DateTimeField | no | indexed |
| `is_forecast` | BooleanField | no | indexed |
| `source` | CharField | no | indexed, 3 choices |
| `provider` | CharField | no | indexed |
| `wave_height_m` | FloatField | yes |  |
| `wave_period_s` | FloatField | yes |  |
| `wave_direction_deg` | FloatField | yes |  |
| `swell_height_m` | FloatField | yes |  |
| `swell_period_s` | FloatField | yes |  |
| `swell_direction_deg` | FloatField | yes |  |
| `wind_wave_height_m` | FloatField | yes |  |
| `wind_speed_kmh` | FloatField | yes |  |
| `wind_gust_kmh` | FloatField | yes |  |
| `wind_direction_deg` | FloatField | yes |  |
| `wind_type` | CharField | no | indexed, 6 choices |
| `sea_level_height_msl_m` | FloatField | yes |  |
| `tide_state` | CharField | no | indexed, 5 choices |
| `air_temperature_c` | FloatField | yes |  |
| `water_temperature_c` | FloatField | yes |  |
| `weather_code` | IntegerField | yes |  |
| `weather_description` | CharField | no |  |
| `uv_index` | FloatField | yes |  |
| `precipitation_mm` | FloatField | yes |  |
| `cloud_cover_pct` | FloatField | yes |  |
| `visibility_km` | FloatField | yes |  |
| `sunrise` | DateTimeField | yes |  |
| `sunset` | DateTimeField | yes |  |
| `raw_payload` | JSONField | no |  |

Composite indexes: (spot, -recorded_at), (spot, is_forecast, recorded_at)

Constraints: sc_cond_unique_reading

#### SurfScore

*surf score* — table `surf_conditions_surfscore`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `condition` | ForeignKey | no | indexed, → surf_conditions.SurfCondition, CASCADE |
| `level` | CharField | no | indexed, 6 choices |
| `score` | PositiveSmallIntegerField | no |  |
| `factors` | JSONField | no |  |
| `recommendation` | TextField | no |  |
| `is_safe_for_level` | BooleanField | no | indexed |
| `is_ai_generated` | BooleanField | no |  |

Composite indexes: (level, -score)

Constraints: sc_score_unique_level


### `training`

#### TrainingCourse

*training course* — table `training_trainingcourse`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `code` | SlugField | no | unique, indexed |
| `title_en` | CharField | no |  |
| `title_tr` | CharField | no |  |
| `description_en` | TextField | no |  |
| `description_tr` | TextField | no |  |
| `icon` | CharField | no |  |
| `estimated_minutes` | PositiveIntegerField | no |  |
| `difficulty` | CharField | no | indexed, 3 choices |
| `required_capability` | CharField | no |  |
| `sort_order` | PositiveIntegerField | no | indexed |
| `is_active` | BooleanField | no | indexed |

Composite indexes: (is_active, sort_order)

#### TrainingLesson

*training lesson* — table `training_traininglesson`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `course` | ForeignKey | no | indexed, → training.TrainingCourse, CASCADE |
| `order` | PositiveIntegerField | no | indexed |
| `title_en` | CharField | no |  |
| `title_tr` | CharField | no |  |
| `summary_en` | TextField | no |  |
| `summary_tr` | TextField | no |  |
| `estimated_minutes` | PositiveIntegerField | no |  |

Composite indexes: (course, order)

Constraints: train_lesson_unique_order

#### TrainingProgress

*training progress* — table `training_trainingprogress`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `user` | ForeignKey | no | indexed, → accounts.User, CASCADE |
| `course` | ForeignKey | no | indexed, → training.TrainingCourse, CASCADE |
| `lesson` | ForeignKey | yes | indexed, → training.TrainingLesson, SET_NULL |
| `step` | ForeignKey | yes | indexed, → training.TrainingStep, SET_NULL |
| `status` | CharField | no | indexed, 3 choices |
| `completed_steps` | JSONField | no |  |
| `started_at` | DateTimeField | yes |  |
| `completed_at` | DateTimeField | yes |  |
| `last_activity_at` | DateTimeField | yes | indexed |

Composite indexes: (user, status)

Constraints: train_progress_unique

#### TrainingStep

*training step* — table `training_trainingstep`

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | BigAutoField | no | PK |
| `created_at` | DateTimeField | no | indexed |
| `updated_at` | DateTimeField | no |  |
| `public_id` | UUIDField | no | unique, indexed |
| `is_deleted` | BooleanField | no | indexed |
| `deleted_at` | DateTimeField | yes |  |
| `created_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `updated_by` | ForeignKey | yes | indexed, → accounts.User, SET_NULL |
| `lesson` | ForeignKey | no | indexed, → training.TrainingLesson, CASCADE |
| `order` | PositiveIntegerField | no | indexed |
| `title_en` | CharField | no |  |
| `title_tr` | CharField | no |  |
| `body_en` | TextField | no |  |
| `body_tr` | TextField | no |  |
| `target_url` | CharField | no |  |
| `action_hint_en` | CharField | no |  |
| `action_hint_tr` | CharField | no |  |
| `image` | FileField | yes |  |

Composite indexes: (lesson, order)

Constraints: train_step_unique_order

