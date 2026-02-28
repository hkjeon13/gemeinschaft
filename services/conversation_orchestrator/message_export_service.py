"""Message export service for conversation history."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.message_history_service import (
    ConversationMessageRecord,
    MessageHistoryService,
)


class MessageExportService:
    def __init__(self, connection: Any):
        self._connection = connection
        self._history_service = MessageHistoryService(connection)

    def export_jsonl(
        self,
        *,
        conversation_id: UUID,
        limit: int = 5000,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> bytes:
        rows: list[ConversationMessageRecord] = self._history_service.list_messages(
            conversation_id=conversation_id,
            limit=limit,
            after_turn_index=after_turn_index,
            status=status,
        )
        payload_lines = [
            json.dumps(
                {
                    "turn_index": row.turn_index,
                    "message_id": str(row.message_id),
                    "participant_id": str(row.participant_id),
                    "participant_name": row.participant_name,
                    "participant_kind": row.participant_kind,
                    "status": row.status,
                    "message_type": row.message_type,
                    "content_text": row.content_text,
                    "metadata": row.metadata,
                    "created_at": row.created_at.isoformat(),
                }
            )
            for row in rows
        ]
        return ("\n".join(payload_lines) + ("\n" if payload_lines else "")).encode("utf-8")
