CREATE TABLE conversation_dataset_version (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    export_job_id UUID NOT NULL REFERENCES export_job(id) ON DELETE CASCADE,
    format TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_conversation_dataset_version UNIQUE (conversation_id, version_no),
    CONSTRAINT uq_conversation_dataset_export_job UNIQUE (export_job_id),
    CONSTRAINT chk_conversation_dataset_format CHECK (
        format IN ('jsonl', 'csv', 'parquet')
    )
);

CREATE INDEX idx_conversation_dataset_version_conversation
    ON conversation_dataset_version (conversation_id, version_no DESC);
