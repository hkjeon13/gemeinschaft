CREATE TABLE topic (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id UUID NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    cluster_key TEXT NOT NULL,
    centroid vector(128) NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_topic_cluster_key UNIQUE (source_document_id, cluster_key),
    CONSTRAINT chk_topic_chunk_count CHECK (chunk_count >= 0)
);

CREATE TABLE source_chunk_topic (
    source_chunk_id UUID NOT NULL REFERENCES source_chunk(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    relevance_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    link_type TEXT NOT NULL DEFAULT 'primary',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_chunk_id, topic_id),
    CONSTRAINT chk_source_chunk_topic_link_type CHECK (
        link_type IN ('primary', 'supporting', 'bridge', 'contradiction')
    )
);

CREATE INDEX idx_topic_source_document_id ON topic (source_document_id);
CREATE INDEX idx_source_chunk_topic_topic_id ON source_chunk_topic (topic_id);
CREATE INDEX idx_source_chunk_topic_source_chunk_id
    ON source_chunk_topic (source_chunk_id);

