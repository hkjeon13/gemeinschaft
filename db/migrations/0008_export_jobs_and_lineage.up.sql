CREATE TABLE export_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    storage_key TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by_user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT chk_export_job_format CHECK (format IN ('jsonl', 'csv', 'parquet')),
    CONSTRAINT chk_export_job_status CHECK (
        status IN ('queued', 'running', 'completed', 'failed')
    )
);

CREATE INDEX idx_export_job_conversation_created
    ON export_job (conversation_id, created_at DESC);
CREATE INDEX idx_export_job_status ON export_job (status);
