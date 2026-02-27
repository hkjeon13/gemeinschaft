# gemeinschaft

Initial repository scaffold for a multi-agent conversation platform.

## Services (scaffold)

- `api_gateway` (`API_GATEWAY_PORT`, default `8000`)
- `conversation_orchestrator` (`CONVERSATION_ORCHESTRATOR_PORT`, default `8001`)
- `agent_runtime` (`AGENT_RUNTIME_PORT`, default `8002`)
- `data_ingestion` (`DATA_INGESTION_PORT`, default `8003`)
- `topic_pipeline` (`TOPIC_PIPELINE_PORT`, default `8004`)
- `export_service` (`EXPORT_SERVICE_PORT`, default `8005`)

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

## Repository layout

- `services/`: service app modules
- `scripts/dev.py`: local multi-service launcher
- `scripts/migrate.py`: SQL migration runner (`up/down/status`)
- `db/migrations/`: versioned Postgres schema migrations
- `tests/`: basic smoke tests for service health and launcher config
- `.github/workflows/ci.yml`: lint/test workflow on pull requests
