"""Event export service for conversation history."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_history_service import (
    ConversationEventRecord,
    EventHistoryService,
)


class EventExportService:
    def __init__(self, connection: Any):
        self._connection = connection
        self._event_history_service = EventHistoryService(connection)

    def export_jsonl(
        self,
        *,
        conversation_id: UUID,
        limit: int = 5000,
        after_seq_no: int = 0,
    ) -> bytes:
        rows: list[ConversationEventRecord] = self._event_history_service.list_events(
            conversation_id=conversation_id,
            limit=limit,
            after_seq_no=after_seq_no,
        )
        payload_lines = [
            json.dumps(
                {
                    "seq_no": row.seq_no,
                    "event_type": row.event_type,
                    "actor_participant_id": (
                        str(row.actor_participant_id)
                        if row.actor_participant_id is not None
                        else None
                    ),
                    "message_id": str(row.message_id) if row.message_id is not None else None,
                    "payload": row.payload,
                    "created_at": row.created_at.isoformat(),
                }
            )
            for row in rows
        ]
        return ("\n".join(payload_lines) + ("\n" if payload_lines else "")).encode("utf-8")
