# gemeinschaft

Initial repository scaffold for a multi-agent conversation platform.

## Services (scaffold)

- `api_gateway` (`API_GATEWAY_PORT`, default `8000`)
- `conversation_orchestrator` (`CONVERSATION_ORCHESTRATOR_PORT`, default `8001`)
- `agent_runtime` (`AGENT_RUNTIME_PORT`, default `8002`)
- `data_ingestion` (`DATA_INGESTION_PORT`, default `8003`)
- `topic_pipeline` (`TOPIC_PIPELINE_PORT`, default `8004`)
- `export_service` (`EXPORT_SERVICE_PORT`, default `8005`)
- `scheduler` (`SCHEDULER_PORT`, default `8006`)

Each service currently exposes `GET /healthz` and `GET /readyz`.

## Local development

1. Create a local environment file:
   - `cp .env.example .env`
2. Install dependencies:
   - `python -m pip install -e ".[dev]"`
3. Start all services:
   - `make dev`
4. Optional autoreload mode:
   - `make dev-reload`

`make dev` starts each service via `uvicorn` and keeps them running in one terminal.
`make dev-reload` enables `uvicorn --reload` for local file-watch development.

## Quality checks

- Lint: `make lint`
- Tests: `make test`
- CI equivalent: `make ci`

## Database migrations

1. Ensure `DATABASE_URL` is set (see `.env.example`).
2. Run schema migrations:
   - Apply all pending: `make db-migrate-up`
   - Revert one step: `make db-migrate-down`
   - Show status: `make db-migrate-status`

The migration runner loads SQL files from `db/migrations` and records applied versions
in `schema_migrations`.

## Event append write-path (PR-03)

Conversation Orchestrator now exposes:

- `POST /internal/events/append`

Behavior:

- append-only writes into `event`
- optimistic sequence check using `expected_seq_no`
- returns `409` on sequence conflict

## Snapshot projector (PR-04)

Conversation Orchestrator also exposes:

- `POST /internal/snapshots/rebuild/{conversation_id}`

Behavior:

- replays immutable `event` rows in `seq_no` order
- rebuilds deterministic snapshot state (`status`, `turn_count`, `last_seq_no`)
- upserts into `conversation_snapshot` read-model table

## Data ingestion upload (PR-05)

Data Ingestion service exposes:

- `POST /internal/sources/upload` (multipart form)

Form fields:

- `tenant_id` (UUID)
- `workspace_id` (UUID)
- `source_type` (`upload|preloaded|integration`, default `upload`)
- `metadata` (optional JSON object as string)
- `file` (upload file)

Behavior:

- stores file bytes in object storage (`OBJECT_STORAGE_PROVIDER=local_fs`)
- computes SHA-256 checksum
- persists metadata row in `source_document`
- returns `source_id`, `storage_key`, checksum, size, and timestamp

## Ingestion parse/chunk worker (PR-06)

Data Ingestion service additionally exposes:

- `POST /internal/sources/{source_id}/process`

Behavior:

- reads uploaded object bytes by `storage_key`
- parses text sources (`text/*` and JSON)
- chunks parsed text into `source_chunk`
- if parsing/loading fails, writes a DLQ record into `ingestion_dlq`

Config:

- `CHUNK_MAX_CHARS` (default `1200`)
- `CHUNK_OVERLAP_CHARS` (default `120`)

## Embedding job + pgvector index (PR-07)

Data Ingestion service additionally exposes:

- `POST /internal/sources/{source_id}/embed`

Behavior:

- loads existing `source_chunk` rows for a source
- generates deterministic local embeddings (model: `EMBEDDING_MODEL`)
- upserts vectors into `source_chunk_embedding`
- writes failures to `ingestion_dlq`

Schema:

- `source_chunk_embedding.embedding` uses `pgvector` type (`vector(128)`)
- ivfflat cosine index is created for vector search

Config:

- `EMBEDDING_MODEL` (default `hash-v1`)
- `EMBEDDING_DIM` (currently fixed to `128` for schema compatibility)

## Topic clustering + topic write (PR-08)

Data Ingestion service additionally exposes:

- `POST /internal/sources/{source_id}/topics`

Behavior:

- loads embedded chunks from `source_chunk` + `source_chunk_embedding`
- clusters chunk embeddings by cosine similarity threshold
- writes topics to `topic` and links to `source_chunk_topic`
- stores clustering failures in `ingestion_dlq`

Config:

- `TOPIC_SIMILARITY_THRESHOLD` (default `0.82`)

## Scheduler + automation template model (PR-09)

Scheduler service exposes:

- `POST /internal/automation/templates`
- `POST /internal/scheduler/runs/trigger`

Behavior:

- stores periodic automation templates (`automation_template`)
- triggers automation runs with deterministic idempotency keys
- duplicate trigger attempts for same schedule slot return `status=duplicate`

## Conversation auto/manual start (PR-10, PR-11)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/start/automation`
- `POST /internal/conversations/start/manual`

Behavior:

- both endpoints use the same internal start path
- writes `conversation` row with `start_trigger=automation|human`
- seeds initial events (`conversation.created`, `conversation.started`)
- automation start supports idempotency by `automation_run_id` (duplicate returns `created=false`)

## Orchestrator loop v1 (PR-12)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/loop/run`

Behavior:

- deterministic round-robin turn creation over conversation participants
- commits `message` + `turn.committed` event pairs
- enforces `max_turns` cap per request

## Agent runtime wrapper (PR-13)

Agent Runtime service additionally exposes:

- `POST /internal/agents/run`

Behavior:

- supports agent profiles (`ai_1`, `ai_2`)
- routes to model by env config with optional request override
- returns normalized runtime output payload (`selected_model`, token estimates, latency)

Config:

- `AGENT_AI_1_MODEL`
- `AGENT_AI_2_MODEL`
- `AGENT_DEFAULT_MODEL`

## Context packet assembler (PR-14)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/context/assemble`

Behavior:

- selects topic (`topic_id` 지정 시 해당 토픽, 없으면 source 기준 대표 토픽)
- assembles recent turns from conversation history
- assembles top evidence chunks from `source_chunk_topic`
- returns a deterministic context packet for downstream agent turn generation

## Validation guard + rejected turns (PR-15)

Conversation Orchestrator loop endpoint now supports:

- `POST /internal/conversations/{conversation_id}/loop/run`
  - new request options:
    - `require_citations` (default `false`)
    - `required_citation_ids` (optional allow-list)

Behavior:

- validates each proposed turn before commit (empty text, repetition loop risk, citation rules)
- writes `turn.committed` for valid turns and `turn.rejected` for invalid turns
- stores validation details in `message.metadata.validation`
- auto-pauses conversation when all turns in the loop run are rejected

## Human intervention controls (PR-16)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/interventions/apply`

Intervention types:

- `interrupt` -> append `human.intervention` + `conversation.paused`
- `resume` -> append `human.intervention` + `conversation.resumed`
- `terminate` -> append `human.intervention` + `conversation.terminated`
- `steer` -> append `human.intervention` only

Loop behavior:

- loop run is blocked for non-`active` conversations (`409`)
- human interventions now become first-class state transitions in event history

## Export jobs + dataset extraction (PR-17)

Export Service now exposes:

- `POST /internal/exports/jobs`
- `GET /internal/exports/jobs/{job_id}`

Behavior:

- validates conversation ownership (`tenant_id`, `workspace_id`, `conversation_id`)
- extracts ordered conversation rows (message + participant metadata)
- serializes dataset to local export storage (`EXPORT_STORAGE_DIR`)
- registers lineage manifest in `export_job` (`schema_version`, conversation metadata, row count)
- supports export formats: `jsonl`, `csv` (current slice)

## Export lineage event append (PR-18)

Export job creation now also appends:

- `event.event_type = export.completed`

Behavior:

- appends `export.completed` at `conversation` next sequence number
- payload includes `export_job_id`, `format`, `storage_key`, `row_count`, `schema_version`
- keeps dataset extraction and event lineage synchronized in one transaction

## Export artifact download (PR-19)

Export Service additionally exposes:

- `GET /internal/exports/jobs/{job_id}/download`

Behavior:

- resolves the export artifact from `storage_key`
- validates artifact path is within configured `EXPORT_STORAGE_DIR`
- returns downloadable file bytes with attachment header

## Runtime-backed loop generation + context injection (PR-20)

Conversation Orchestrator loop endpoint now supports additional request options:

- `source_document_id`, `topic_id`
- `context_turn_window`, `context_evidence_limit`
- `use_agent_runtime`, `agent_max_output_tokens`

Behavior:

- assembles context packet during loop turns when `source_document_id` is provided
- optionally calls Agent Runtime (`AGENT_RUNTIME_BASE_URL`) for AI participant turn generation
- stores generation metadata (`generator`, model/tokens/latency) in `message.metadata.generation`
- uses context evidence chunk IDs as citation allow-list when explicit citation IDs are omitted

## Human approval workflow for AI turns (PR-21)

Conversation Orchestrator adds approval controls:

- `POST /internal/conversations/{conversation_id}/turns/{turn_index}/approval`

Loop behavior update:

- when `require_human_approval=true`, valid AI turns are stored as `message.status=proposed`
- pending turns emit `turn.pending_approval` instead of immediate `turn.committed`

Approval behavior:

- `decision=approve` -> `turn.approved` + `turn.committed`, message status becomes `committed`
- `decision=reject` -> `turn.rejected`, message status becomes `rejected`

## Loop guard + disagreement arbitration (PR-22)

Loop request options:

- `max_consecutive_rejections`
- `arbitration_enabled`
- `pause_on_disagreement`

Behavior:

- stops early and emits `loop.guard_triggered` when consecutive rejected turns reach threshold
- emits `turn.arbitration_requested` when consecutive committed AI turns cite disjoint evidence sets
- can auto-pause conversation on arbitration (`conversation.paused`)

## Dataset version lineage on export (PR-23)

Export flow now records dataset versions:

- new table: `conversation_dataset_version`
- version is scoped per conversation and auto-increments (`version_no`)

Behavior:

- each successful export registers a version lineage row linked to `export_job`
- `export_job.manifest` includes `dataset_version_no`
- `export.completed` event payload includes `dataset_version_no`

## Dataset version listing API (PR-24)

Export Service additionally exposes:

- `GET /internal/conversations/{conversation_id}/exports/versions?limit=20`

Behavior:

- returns dataset versions in descending `version_no`
- includes `export_job_id`, format, storage key, row count, and manifest lineage fields

## Dataset version detail + download APIs (PR-25)

Export Service additionally exposes:

- `GET /internal/conversations/{conversation_id}/exports/versions/latest`
- `GET /internal/conversations/{conversation_id}/exports/versions/{version_no}`
- `GET /internal/conversations/{conversation_id}/exports/versions/latest/download`
- `GET /internal/conversations/{conversation_id}/exports/versions/{version_no}/download`

Behavior:

- supports fetching latest/specific dataset version metadata
- supports downloading artifacts by logical dataset version

## Pending Approval Queue API (PR-26)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/turns/pending-approval?limit=20`

Behavior:

- returns `message.status=proposed` turns in ascending turn order
- includes participant metadata and message payload for moderator review

## Steering-aware turn generation (PR-27)

Loop behavior update:

- reads latest `human.intervention` with `intervention_type=steer`
- injects steering instruction into AI generation prompt
- stores steering instruction in `message.metadata.generation`

## Conversation Ops Summary API (PR-28)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/ops/summary`

Behavior:

- returns operational counts (participants/messages by status)
- includes latest event pointer (`last_event_seq_no`, `last_event_type`, `last_event_at`)

## Scheduler Execute Flow (PR-29)

Scheduler additionally exposes:

- `POST /internal/scheduler/runs/execute`

Behavior:

- triggers automation run idempotently
- optionally auto-starts conversation via Orchestrator (`CONVERSATION_ORCHESTRATOR_BASE_URL`)
- maps template participant tokens into orchestrator participant seeds

## Scheduler Run Preview API (PR-30)

Scheduler additionally exposes:

- `POST /internal/scheduler/runs/preview`

Behavior:

- normalizes schedule timestamp and computes idempotency key
- returns mapped participant seeds and start payload preview before execution

## Scheduler Run History API (PR-31)

Scheduler additionally exposes:

- `GET /internal/automation/templates/{template_id}/runs?limit=20`

Behavior:

- returns recent run history ordered by schedule/run id
- includes idempotency key, status, trigger time, and metadata

## Participant Role Switching API (PR-32)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/participants/{participant_id}/role/switch`

Behavior:

- updates `participant.role_label` with moderator/system initiated role changes
- appends `participant.role_switched` event containing previous/new role and reason metadata
- validates conversation/participant existence and rejects empty or unchanged role labels

## Participant Moderation API (PR-33)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/participants/{participant_id}/moderation`

Behavior:

- supports moderation actions: `mute`, `unmute`
- appends `participant.muted` / `participant.unmuted` events with reason and metadata
- loop runner excludes muted participants from turn generation rotation

## Derailment Guard in Loop Validation (PR-34)

Conversation Orchestrator loop API now supports:

- `derailment_guard_enabled` (default `false`)
- `min_topic_keyword_matches` (default `1`)

Behavior:

- AI turns are rejected as `topic_derailment` when generated content does not align with objective/topic keywords
- objective/topic/steering keywords are injected into validation context for alignment scoring
- strengthens containment for off-topic drift before commit

## Rejected Turns Review Queue API (PR-35)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/turns/rejected?limit=20`

Behavior:

- returns rejected turns with validation failure type/reasons for moderation review
- includes participant metadata and original message payload
- ordered by latest turn first for rapid triage

## Conversation Failure Summary API (PR-36)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/ops/failures`

Behavior:

- aggregates rejected-turn failure types (`missing_citation`, `invalid_citation`, `loop_risk_repetition`, `topic_derailment`)
- includes event-level containment signals (`loop.guard_triggered`, `turn.arbitration_requested`)
- provides a compact ops read model for moderation dashboards and alerting

## Participant Roster API (PR-37)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/participants?include_left=false`

Behavior:

- returns participant roster with role labels and moderation mute state
- defaults to active participants only (`left_at IS NULL`)
- can include historical/left participants when `include_left=true`

## Batch Turn Approval API (PR-38)

Conversation Orchestrator additionally exposes:

- `POST /internal/conversations/{conversation_id}/turns/approval/batch`

Behavior:

- applies multiple turn approval/rejection decisions in a single moderation action
- returns per-turn success/failure results with error codes (`turn_not_found`, `invalid_decision`, etc.)
- supports `stop_on_error` for fail-fast review mode

## Conversation Event History API (PR-39)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/events?limit=50&after_seq_no=0`

Behavior:

- returns ordered event history for audit/debug (`seq_no` ascending)
- supports incremental polling via `after_seq_no`
- includes actor/message linkage and full event payload

## Conversation Event Download API (PR-40)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/events/download?limit=5000&after_seq_no=0`

Behavior:

- exports conversation events as JSONL (`application/x-ndjson`)
- supports incremental export windows via `after_seq_no`
- returns attachment headers for direct archive/download workflows

## Automation Template Listing API (PR-41)

Scheduler additionally exposes:

- `GET /internal/automation/templates?tenant_id=...&workspace_id=...&include_disabled=false&limit=50`

Behavior:

- returns workspace-scoped automation templates with scheduling metadata
- supports filtering disabled templates via `include_disabled`
- sorted by latest template updates

## Automation Template Detail API (PR-42)

Scheduler additionally exposes:

- `GET /internal/automation/templates/{template_id}`

Behavior:

- returns full template configuration (`objective`, `rrule`, participants, metadata)
- includes template lifecycle timestamps (`created_at`, `updated_at`)

## Automation Template Enabled Toggle API (PR-43)

Scheduler additionally exposes:

- `PATCH /internal/automation/templates/{template_id}/enabled`

Behavior:

- toggles periodic automation on/off without deleting template configuration
- updates `updated_at` for operational auditability

## Automation Template Patch API (PR-44)

Scheduler additionally exposes:

- `PATCH /internal/automation/templates/{template_id}`

Behavior:

- supports partial updates for template name/objective/rrule/participants/metadata
- validates that at least one mutable field is provided

## Execute Failure Persistence (PR-45)

Scheduler execute flow update:

- on orchestrator start failure, run status is persisted as `failed`
- stores orchestrator error details into `automation_run.error_message` and run metadata

## Scheduler Batch Execute API (PR-46)

Scheduler additionally exposes:

- `POST /internal/scheduler/runs/execute-batch`

Behavior:

- executes multiple templates for the same schedule timestamp in one request
- returns per-template success/error results with itemized error codes
- reuses single-run execute semantics (idempotent trigger + optional orchestrator start)

## Scheduler Run Detail + Retry APIs (PR-47, PR-48)

Scheduler additionally exposes:

- `GET /internal/scheduler/runs/{run_id}`
- `POST /internal/scheduler/runs/{run_id}/retry`

Behavior:

- retrieves individual run state and metadata for audit/debug
- retries only `failed` runs (`409` for non-retryable statuses)
- retry flow delegates to execute logic with merged run metadata

## Conversation Message History API (PR-49)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/messages?limit=50&after_turn_index=0&status=...`

Behavior:

- returns ordered message history with participant and status metadata
- supports incremental pagination by `turn_index`
- supports optional message status filter (`proposed`, `validated`, `rejected`, `committed`)

## Conversation Message Download API (PR-50)

Conversation Orchestrator additionally exposes:

- `GET /internal/conversations/{conversation_id}/messages/download?limit=5000&after_turn_index=0&status=...`

Behavior:

- exports message history as JSONL (`application/x-ndjson`)
- supports incremental/status-scoped dataset extraction
- returns attachment headers for downstream dataset pipelines

## Internal Auth Middleware (PR-51)

Shared app factory now applies internal auth middleware:

- `INTERNAL_API_TOKEN` is configured, non-health endpoints require `x-internal-api-token`
- auth context is attached to request state (`role`, principal, tenant/workspace scope headers)

## Tenant/Workspace Scope Guard (PR-52)

Scope guard behavior:

- scheduler template endpoints enforce `x-auth-tenant-id` / `x-auth-workspace-id` against resource scope
- conversation start endpoints enforce scope against request `tenant_id` / `workspace_id`

## Role-Based Access Guard (PR-53)

Role guard behavior:

- mutating endpoints require `admin/operator/system` roles
- read endpoints allow `viewer` role
- role is resolved from `x-internal-role` request header

## Request Context + Trace Header (PR-54)

Shared app factory now also:

- propagates `x-request-id` (or generates one)
- returns `x-request-id` on responses
- emits request-level structured logs with latency

## Security Regression Tests (PR-55)

Added tests for:

- internal auth token enforcement/bypass rules for health endpoints
- scope mismatch rejection (`403`) and viewer-role write blocking
- request-id propagation guarantees

## Conversation Scope Authorization (PR-56)

Conversation Orchestrator auth guard update:

- `conversation_id` 기반 API는 `conversation.tenant_id/workspace_id`를 조회해 scope 검증
- `x-auth-tenant-id`, `x-auth-workspace-id` 헤더와 리소스 스코프가 다르면 `403`
- scope 헤더 미사용 시 기존 내부 API 동작은 유지

## Export Service Role/Scope Guard (PR-57)

Export Service auth guard update:

- export 생성은 `admin/operator/system` 역할만 허용
- export job 조회/다운로드는 `viewer` 포함 read 권한 허용
- job 리소스(`tenant_id/workspace_id`) 기준 scope mismatch 시 `403`

## Data Ingestion Role/Scope Guard (PR-58)

Data Ingestion auth guard update:

- source upload/process/embed/topic 작업은 `admin/operator/system` 역할로 제한
- source 기반 API는 `source_document.tenant_id/workspace_id` 조회 후 scope 검증
- scope mismatch 시 작업 실행 전에 `403` 반환

## Agent Runtime Role Guard (PR-59)

Agent Runtime auth guard update:

- `POST /internal/agents/run`는 `admin/operator/system` 역할만 허용
- viewer role은 `403`으로 차단

## Internal Auth Configuration Docs (PR-60)

Configuration/docs update:

- `.env.example`에 `INTERNAL_API_TOKEN` 및 내부 인증 헤더 규약 추가
- health/readiness 제외 내부 API 보호 모델(`x-internal-api-token`) 명시
- 역할/스코프 헤더 (`x-internal-role`, `x-auth-tenant-id`, `x-auth-workspace-id`) 운영 기준 정리

## Query Performance Indexes (PR-61)

Scalability hardening migration:

- `message` 조회 경로 최적화를 위한 `(conversation_id, status, turn_index)` 및 `(conversation_id, turn_index DESC)` 인덱스 추가
- active participant/roster 조회를 위한 `(conversation_id, left_at, joined_at, id)` 인덱스 추가
- 스케줄러 템플릿/실행 이력 조회를 위한 복합 정렬 인덱스 추가
  - `automation_template (tenant_id, workspace_id, updated_at DESC, id DESC)`
  - `automation_run (template_id, scheduled_for DESC, id DESC)`

## Pytest Asyncio Scope Pinning (PR-62)

Test stability hardening:

- `pyproject.toml`의 pytest 설정에 `asyncio_default_fixture_loop_scope = "function"` 추가
- 향후 `pytest-asyncio` 기본값 변경 시 발생 가능한 fixture loop scope 회귀를 선제 차단

## Scheduler Outbound Auth/Trace Propagation (PR-63)

Scheduler to Orchestrator execution hardening:

- 스케줄러의 오케스트레이터 호출에 내부 인증/스코프/트레이스 헤더 전달
  - `x-internal-api-token`
  - `x-internal-role`
  - `x-auth-tenant-id`
  - `x-auth-workspace-id`
  - `x-request-id`
- 실행 요청 경로는 inbound `x-request-id`를 재사용하고, 배치 실행 경로는 `scheduler-run-<run_id>` fallback trace id 생성
- 환경변수 추가
  - `ORCHESTRATOR_INTERNAL_API_TOKEN` (미지정 시 `INTERNAL_API_TOKEN` 재사용)
  - `SCHEDULER_INTERNAL_ROLE` (기본값 `system`)

## Scheduler Scope-First Authorization (PR-64)

Security ordering fix for scheduler write endpoints:

- `trigger/execute/template-update/template-enabled` 경로에서 리소스 스코프 검증을 쓰기 작업 이전으로 이동
- `execute-batch`와 `retry`도 요청 컨텍스트를 내부 실행 경로로 전달해 동일한 scope/auth 규칙 적용
- scope mismatch(`403`) 시 `automation_run` 생성/수정이 발생하지 않도록 회귀 테스트 추가

## Scheduler Batch Error Classification (PR-65)

Batch execution API observability improvement:

- `/internal/scheduler/runs/execute-batch`에서 scope 거부(`403`)를 `error_code="forbidden_scope"`로 명시
- 템플릿 미존재(`template_not_found`), 오케스트레이터 실패(`orchestrator_error`)와 구분해 운영 원인 파악 용이성 강화

## Request-ID on Auth Failures (PR-66)

Cross-service traceability hardening:

- 공통 미들웨어 순서를 조정해 인증 실패(`401`) 및 헤더 검증 실패(`400`) 응답에도 `x-request-id`가 일관되게 포함되도록 보장
- 내부 인증 실패 상황에서도 요청 단위 로그/추적 상관관계가 유지되도록 회귀 테스트 보강

## Scheduler Principal Propagation (PR-67)

Internal caller identity propagation:

- 스케줄러의 오케스트레이터 호출에 `x-internal-principal-id` 헤더를 포함해 호출 주체를 명시
- 기본 principal id는 `scheduler-service`이며 `SCHEDULER_INTERNAL_PRINCIPAL_ID`로 override 가능
- role/scope/token/request-id와 함께 principal까지 전달되어 감사 추적 일관성 강화

## Scheduler Mutation Scope Regression Tests (PR-68)

Security regression coverage expansion:

- 템플릿 변경 API(`PATCH /templates/{id}`, `PATCH /templates/{id}/enabled`)에서 scope mismatch 시 쓰기 메서드가 호출되지 않음을 검증
- `execute/trigger/execute-batch`에 이어 템플릿 변경 경로까지 “scope 검증 후 쓰기” 정책을 테스트로 고정

## Scheduler Outbound Token Precedence Tests (PR-69)

Configuration regression coverage:

- `ORCHESTRATOR_INTERNAL_API_TOKEN`이 설정되면 `INTERNAL_API_TOKEN`보다 우선 적용되는지 검증
- override가 없을 때 `INTERNAL_API_TOKEN` fallback과 기본 role/principal(`system`, `scheduler-service`) 적용을 검증

## Bad Header Trace Propagation Test (PR-70)

Auth middleware trace regression coverage:

- 내부 scope 헤더 파싱 실패(`400`) 응답에서도 `x-request-id`가 누락되지 않는지 검증
- 잘못된 헤더 입력 상황도 성공/실패 로그와 동일한 trace 키로 연결 가능하도록 보장

## Scheduler Orchestrator Retry Policy (PR-71)

Outbound call resilience hardening:

- 오케스트레이터 클라이언트에 timeout/retry/backoff 설정 추가
  - `ORCHESTRATOR_HTTP_TIMEOUT_SECONDS`
  - `ORCHESTRATOR_HTTP_MAX_RETRIES`
  - `ORCHESTRATOR_HTTP_RETRY_BACKOFF_SECONDS`
- 일시적 네트워크 오류/`5xx`에 대해 재시도하고, `4xx`는 즉시 실패하도록 정책 분리
- 재시도 성공/소진/비재시도(`400`) 케이스를 단위 테스트로 검증

## Scheduler Orchestrator Config Validation (PR-72)

Fail-fast configuration guard:

- 오케스트레이터 HTTP 설정(`timeout/retries/backoff`) 파싱 실패 시 명확한 `500` 설정 오류 반환
- 음수 retry/backoff, 비양수 timeout 등 잘못된 값에 대해 원인별 에러 메시지 제공
- 설정 회귀를 막기 위한 단위 테스트 추가

## Scheduler Read-Path Scope Ordering (PR-73)

Scope-first ordering applied to run history read path:

- `GET /internal/automation/templates/{template_id}/runs`에서 scope 검증 후 `list_runs` 조회 수행
- scope mismatch(`403`) 시 run 목록 조회 쿼리가 실행되지 않음을 회귀 테스트로 검증

## Export Download Scope-First Authorization (PR-74)

Export artifact read-path hardening:

- `/internal/exports/jobs/{job_id}/download`에서 scope 검증을 아티팩트 read 이전에 수행
- scope mismatch(`403`) 요청은 파일/스토리지 read를 수행하지 않도록 차단
- 다운로드 허용/거부 경로 모두 회귀 테스트로 검증

## Dataset Version Scope Guard (PR-75)

Conversation dataset version API security hardening:

- `/internal/conversations/{conversation_id}/exports/versions*` 경로에 conversation scope 검증 추가
- scope 헤더 사용 시 conversation의 tenant/workspace 조회 후 mismatch(`403`) 차단
- 버전 조회/다운로드 경로에서 scope 거부 시 dataset artifact read가 실행되지 않도록 회귀 테스트 보강

## Dataset Version Scope Happy-Path Tests (PR-76)

Scope guard completeness test expansion:

- matching scope에서 dataset version 목록/최신 다운로드가 `200`으로 동작함을 검증
- 차단 케이스(`403`)와 허용 케이스(`200`)를 모두 고정해 scope 검증 회귀 안정성 강화

## Export Repository Conversation Scope Tests (PR-77)

Repository-level regression coverage:

- `get_conversation_scope` 메서드의 conversation scope 조회 성공/미존재 실패 케이스 단위 테스트 추가
- app 레벨 scope guard가 의존하는 repository 계약을 하위 계층 테스트로 고정

## Scheduler Client Config Fail-Fast Ordering (PR-78)

Write safety hardening for execute flow:

- `auto_start_conversation=True` 실행에서 오케스트레이터 클라이언트 설정 검증을 run 생성 이전에 수행
- 잘못된 orchestrator HTTP 설정 시 `automation_run` 레코드가 생성되지 않도록 순서 보정
- 관련 회귀 테스트로 “설정 오류 시 no-write” 동작 고정

## Scheduler Batch Config Error Code (PR-79)

Batch execution diagnostics refinement:

- `execute-batch`에서 orchestrator client 설정 오류(`500`)를 `orchestrator_config_error`로 분리
- 배치 실행 결과에서 운영 설정 오류와 일반 실행 오류를 구분해 원인 파악 시간을 단축
- 설정 오류 케이스에서도 run 생성이 발생하지 않음을 테스트로 검증

## Shared DB Connection Pooling (PR-80)

Connection scalability hardening:

- `services/shared/db.py` 공통 DB 연결 모듈 추가 (선택적 `psycopg_pool` 기반)
- `conversation_orchestrator`, `scheduler`, `export_service`, `data_ingestion`의 `_connect()`를 공통 모듈로 통합
- 풀 설정 추가
  - `DB_POOL_ENABLED`
  - `DB_POOL_MIN_SIZE`
  - `DB_POOL_MAX_SIZE`
- `DB_POOL_TIMEOUT_SECONDS`
- `psycopg_pool` 미설치 시 direct connection fallback 유지
- 공통 풀 모듈 단위 테스트 추가 (pool 사용, fallback, 설정 검증)

## Cursor Pagination for History APIs (PR-81)

Read API scalability update:

- 신규 cursor 페이지네이션 엔드포인트 추가
  - `GET /internal/conversations/{conversation_id}/events/page`
  - `GET /internal/conversations/{conversation_id}/messages/page`
- cursor 규약
  - events: `seq:<number>`
  - messages: `turn:<number>`
- 응답은 `{ items, next_cursor }` 형태를 제공하고, `limit + 1` 조회 기반으로 `next_cursor` 계산
- 기존 offset-like(`after_seq_no`, `after_turn_index`) API는 호환성 유지

## Scheduler Batch Duplicate Guard (PR-82)

Batch execution input hardening:

- `BatchExecuteAutomationRunsRequest.template_ids`에 중복 ID 금지 validator 추가
- 동일 template이 한 배치에 중복 포함되어 중복 트리거/오해를 유발하는 입력을 `422`로 사전 차단
- 관련 API 검증 테스트 추가

## Shared DB Pool Shutdown Hook (PR-83)

Resource lifecycle hardening:

- 공통 `build_service_app()`에 shutdown 훅을 추가해 프로세스 종료 시 shared DB pool 정리
- `close_all_db_pools()` 호출을 테스트로 고정해 재시작/배포 시 커넥션 누수 위험 완화

## App Lifespan Migration (PR-84)

Framework compatibility update:

- 공통 앱 팩토리의 종료 처리 로직을 `on_event("shutdown")`에서 FastAPI `lifespan`으로 전환
- 기존 pool 정리 동작은 유지하면서 deprecation warning 제거

## Cursor Support for Download APIs (PR-85)

History export API consistency update:

- 메시지/이벤트 다운로드 API에 `cursor` 파라미터 지원 추가
  - messages: `cursor=turn:<number>`
  - events: `cursor=seq:<number>`
- 기존 `after_turn_index`/`after_seq_no`와 함께 사용 시 값 충돌은 `400`으로 차단
- 커서 적용 및 충돌 검증 케이스 테스트 추가

## Pool Requirement Mode (PR-86)

DB pooling deployment control:

- `DB_POOL_REQUIRE` 설정 추가
- `DB_POOL_ENABLED=true` 상태에서 `DB_POOL_REQUIRE=true`이면 `psycopg_pool` 미설치 시 즉시 실패(fail-fast)
- 운영 환경에서 의도치 않은 direct-connection fallback을 방지하도록 테스트 추가

## Dataset Version Cursor Pagination (PR-87)

Export dataset history scalability update:

- 신규 엔드포인트 추가: `GET /internal/conversations/{conversation_id}/exports/versions/page`
- cursor 규약: `v:<version_no>`
- repository `list_dataset_versions`에 `before_version_no` 필터 지원을 추가해 version-desc 커서 조회 구현
- scope guard 및 invalid cursor 검증 테스트 추가

## Scheduler Template Cursor Pagination (PR-88)

Scheduler template listing scalability update:

- 신규 엔드포인트 추가: `GET /internal/automation/templates/page`
- cursor 규약: `u:<updated_at_iso>|<template_id>`
- repository `list_templates`에 keyset 필터(`before_updated_at`, `before_template_id`) 추가
- scope/auth 및 invalid cursor 회귀 테스트 추가

## Scheduler Run Cursor Pagination (PR-89)

Scheduler run history pagination update:

- 신규 엔드포인트 추가: `GET /internal/automation/templates/{template_id}/runs/page`
- cursor 규약: `r:<scheduled_for_iso>|<run_id>`
- repository `list_runs`에 keyset 필터(`before_scheduled_for`, `before_run_id`) 추가
- scope mismatch no-query, invalid cursor, repository cursor SQL 테스트 추가

## URL-Safe Cursor Timestamp Encoding (PR-90)

Cursor interoperability hardening:

- 스케줄러 커서(`u:*`, `r:*`)의 timestamp를 UTC `Z` 형식으로 인코딩하도록 변경
- cursor parser는 `Z`와 `+00:00` 모두 허용
- `+` 문자가 쿼리스트링에서 공백으로 해석되는 URL 인코딩 이슈를 예방

## Pool Disable Path Test (PR-91)

DB connection mode regression coverage:

- `DB_POOL_ENABLED=false`일 때 pool 모듈이 있어도 direct connection 경로를 사용하는지 테스트 추가
- 운영 중 풀 비활성화 롤백 시 동작 일관성 보장

## Pagination `has_more` Field (PR-92)

Page API ergonomics update:

- 커서 페이지네이션 응답에 `has_more` boolean 필드 추가
  - conversation events/messages page
  - export dataset versions page
  - scheduler templates/runs page
- `next_cursor`와 함께 클라이언트의 페이지 제어 로직 단순화

## Export Job Cursor Pagination (PR-93)

Export job history pagination update:

- 신규 엔드포인트 추가: `GET /internal/conversations/{conversation_id}/exports/jobs/page`
- cursor 규약: `j:<created_at_iso>|<job_id>`
- repository `list_export_jobs`에 keyset 필터(`before_created_at`, `before_job_id`) 추가
- conversation scope guard 및 invalid cursor 회귀 테스트 추가

## Pending Approval Queue Cursor Pagination (PR-94)

Pending approval read-path scalability update:

- 신규 엔드포인트 추가: `GET /internal/conversations/{conversation_id}/turns/pending-approval/page`
- cursor 규약: `turn:<turn_index>`
- `PendingTurnService.list_pending_turns`에 `after_turn_index` 필터 추가
- `limit + 1` 기반의 `next_cursor`, `has_more` 계산 및 invalid cursor 테스트 추가

## Rejected Turn Queue Cursor Pagination (PR-95)

Rejected turn review queue pagination update:

- 신규 엔드포인트 추가: `GET /internal/conversations/{conversation_id}/turns/rejected/page`
- cursor 규약: `turn:<turn_index>` (descending queue)
- `RejectedTurnService.list_rejected_turns`에 `before_turn_index` 필터 추가
- service cursor SQL/validation 및 API page/invalid cursor 테스트 추가

## Participant Roster Cursor Pagination (PR-96)

Participant roster read-path pagination update:

- 신규 엔드포인트 추가: `GET /internal/conversations/{conversation_id}/participants/page`
- cursor 규약: `p:<joined_at_iso>|<participant_id>`
- `ParticipantRosterService.list_participants`에 `limit`, `after_joined_at`, `after_participant_id` 필터 추가
- include_left 분기별 cursor SQL, invalid cursor, page 응답(`next_cursor`, `has_more`) 테스트 추가

## Conversation Scope Regression for New Page APIs (PR-97)

Authorization regression hardening:

- 신규 page API 3종에 대해 scope mismatch 시 서비스 조회 전에 403 차단되는지 테스트 추가
  - `GET /turns/pending-approval/page`
  - `GET /turns/rejected/page`
  - `GET /participants/page`
- scope match 시 정상 200 응답과 서비스 호출 경로도 함께 검증

## Data Ingestion Source Listing Page API (PR-98)

Ingestion source catalog read-path 추가:

- 신규 엔드포인트 추가: `GET /internal/sources/page`
- cursor 규약: `s:<created_at_iso>|<source_id>`
- `SourceRepository.list_sources` 추가
  - tenant/workspace scope 기반 조회
  - optional `source_type` 필터
  - keyset cursor(`before_created_at`, `before_source_id`) 지원
- API/repository/scope guard 회귀 테스트 추가

## Repository layout

- `services/`: service app modules
- `scripts/dev.py`: local multi-service launcher
- `scripts/migrate.py`: SQL migration runner (`up/down/status`)
- `db/migrations/`: versioned Postgres schema migrations
- `tests/`: basic smoke tests for service health and launcher config
- `.github/workflows/ci.yml`: lint/test workflow on pull requests
