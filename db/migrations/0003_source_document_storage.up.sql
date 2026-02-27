CREATE TABLE source_document (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'upload',
    original_filename TEXT NOT NULL,
    content_type TEXT,
    byte_size BIGINT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    storage_provider TEXT NOT NULL DEFAULT 'local_fs',
    storage_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_source_document_type CHECK (
        source_type IN ('upload', 'preloaded', 'integration')
    ),
    CONSTRAINT uq_source_document_storage_key UNIQUE (storage_key)
);

CREATE INDEX idx_source_document_tenant_workspace
    ON source_document (tenant_id, workspace_id);
CREATE INDEX idx_source_document_checksum ON source_document (checksum_sha256);
CREATE INDEX idx_source_document_created_at ON source_document (created_at);

