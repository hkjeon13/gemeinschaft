CREATE TABLE conversation_snapshot (
    conversation_id UUID PRIMARY KEY REFERENCES conversation(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft',
    last_seq_no BIGINT NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_snapshot_status CHECK (
        status IN (
            'draft',
            'prepared',
            'active',
            'paused',
            'completed',
            'curated',
            'versioned',
            'archived'
        )
    )
);

CREATE INDEX idx_conversation_snapshot_status ON conversation_snapshot (status);
CREATE INDEX idx_conversation_snapshot_last_seq_no
    ON conversation_snapshot (last_seq_no);

