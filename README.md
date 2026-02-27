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

## Repository layout

- `services/`: service app modules
- `scripts/dev.py`: local multi-service launcher
- `scripts/migrate.py`: SQL migration runner (`up/down/status`)
- `db/migrations/`: versioned Postgres schema migrations
- `tests/`: basic smoke tests for service health and launcher config
- `.github/workflows/ci.yml`: lint/test workflow on pull requests
