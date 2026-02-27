CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE source_chunk_embedding (
    id BIGSERIAL PRIMARY KEY,
    source_chunk_id UUID NOT NULL REFERENCES source_chunk(id) ON DELETE CASCADE,
    embedding vector(128) NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL DEFAULT 128,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_chunk_embedding UNIQUE (source_chunk_id),
    CONSTRAINT chk_source_chunk_embedding_dim CHECK (embedding_dim = 128)
);

CREATE INDEX idx_source_chunk_embedding_source_chunk_id
    ON source_chunk_embedding (source_chunk_id);
CREATE INDEX idx_source_chunk_embedding_vector
    ON source_chunk_embedding
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

