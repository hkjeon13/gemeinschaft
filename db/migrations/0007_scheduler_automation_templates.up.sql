CREATE TABLE automation_template (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    name TEXT NOT NULL,
    conversation_objective TEXT NOT NULL,
    rrule TEXT NOT NULL,
    participants JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_automation_template_name UNIQUE (tenant_id, workspace_id, name)
);

CREATE TABLE automation_run (
    id BIGSERIAL PRIMARY KEY,
    template_id UUID NOT NULL REFERENCES automation_template(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'triggered',
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_automation_run_status CHECK (
        status IN ('triggered', 'duplicate', 'failed')
    ),
    CONSTRAINT uq_automation_run_idempotency UNIQUE (template_id, idempotency_key)
);

CREATE INDEX idx_automation_template_enabled ON automation_template (enabled);
CREATE INDEX idx_automation_run_template_id ON automation_run (template_id);
CREATE INDEX idx_automation_run_scheduled_for ON automation_run (scheduled_for);

