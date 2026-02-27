CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    title TEXT NOT NULL,
    objective TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    start_trigger TEXT NOT NULL DEFAULT 'human',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_conversation_status CHECK (
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
    ),
    CONSTRAINT chk_conversation_start_trigger CHECK (
        start_trigger IN ('automation', 'human')
    )
);

CREATE TABLE participant (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    user_id UUID,
    agent_profile_id UUID,
    display_name TEXT NOT NULL,
    role_label TEXT,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_participant_kind CHECK (kind IN ('human', 'ai', 'system'))
);

CREATE TABLE message (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id UUID NOT NULL REFERENCES participant(id) ON DELETE RESTRICT,
    turn_index INTEGER NOT NULL,
    parent_message_id UUID REFERENCES message(id) ON DELETE SET NULL,
    message_type TEXT NOT NULL DEFAULT 'statement',
    status TEXT NOT NULL DEFAULT 'committed',
    content_text TEXT NOT NULL,
    context_packet_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_message_type CHECK (
        message_type IN ('statement', 'question', 'challenge', 'synthesis', 'system')
    ),
    CONSTRAINT chk_message_status CHECK (
        status IN ('proposed', 'validated', 'rejected', 'committed')
    ),
    CONSTRAINT uq_message_turn UNIQUE (conversation_id, turn_index)
);

CREATE TABLE event (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    message_id UUID REFERENCES message(id) ON DELETE SET NULL,
    actor_participant_id UUID REFERENCES participant(id) ON DELETE SET NULL,
    seq_no BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_sequence UNIQUE (conversation_id, seq_no)
);

CREATE INDEX idx_conversation_tenant_workspace
    ON conversation (tenant_id, workspace_id);
CREATE INDEX idx_conversation_status ON conversation (status);
CREATE INDEX idx_conversation_created_at ON conversation (created_at);

CREATE INDEX idx_participant_conversation_id ON participant (conversation_id);
CREATE INDEX idx_participant_kind ON participant (kind);

CREATE INDEX idx_message_conversation_id ON message (conversation_id);
CREATE INDEX idx_message_participant_id ON message (participant_id);
CREATE INDEX idx_message_created_at ON message (created_at);

CREATE INDEX idx_event_conversation_created_at
    ON event (conversation_id, created_at);
CREATE INDEX idx_event_type ON event (event_type);

