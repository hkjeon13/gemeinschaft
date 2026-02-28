CREATE INDEX idx_message_conversation_status_turn
    ON message (conversation_id, status, turn_index);

CREATE INDEX idx_message_conversation_turn_desc
    ON message (conversation_id, turn_index DESC);

CREATE INDEX idx_participant_conversation_active_joined
    ON participant (conversation_id, left_at, joined_at, id);

CREATE INDEX idx_automation_template_scope_updated
    ON automation_template (tenant_id, workspace_id, updated_at DESC, id DESC);

CREATE INDEX idx_automation_run_template_scheduled_desc
    ON automation_run (template_id, scheduled_for DESC, id DESC);
