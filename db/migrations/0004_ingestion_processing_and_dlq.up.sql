CREATE TABLE source_chunk (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id UUID NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    content_text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_chunk_order UNIQUE (source_document_id, chunk_index),
    CONSTRAINT chk_source_chunk_range CHECK (char_start >= 0 AND char_end >= char_start)
);

CREATE TABLE ingestion_dlq (
    id BIGSERIAL PRIMARY KEY,
    source_document_id UUID REFERENCES source_document(id) ON DELETE SET NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    retryable BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_source_chunk_document_id ON source_chunk (source_document_id);
CREATE INDEX idx_source_chunk_created_at ON source_chunk (created_at);

CREATE INDEX idx_ingestion_dlq_source_document_id
    ON ingestion_dlq (source_document_id);
CREATE INDEX idx_ingestion_dlq_created_at ON ingestion_dlq (created_at);

