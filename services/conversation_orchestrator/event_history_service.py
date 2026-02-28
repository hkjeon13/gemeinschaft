"""Read-model service for conversation event history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class ConversationEventRecord:
    seq_no: int
    event_type: str
    actor_participant_id: UUID | None
    message_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime


class EventHistoryService:
    def __init__(self, connection: Any):
        self._connection = connection

    def list_events(
        self,
        *,
        conversation_id: UUID,
        limit: int = 50,
        after_seq_no: int = 0,
    ) -> list[ConversationEventRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if after_seq_no < 0:
            raise ValueError("after_seq_no must be >= 0")

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM conversation
                WHERE id = %s
                """,
                (str(conversation_id),),
            )
            if cursor.fetchone() is None:
                raise ConversationNotFoundError(f"Conversation {conversation_id} not found")

            cursor.execute(
                """
                SELECT
                    seq_no,
                    event_type,
                    actor_participant_id,
                    message_id,
                    payload,
                    created_at
                FROM event
                WHERE conversation_id = %s AND seq_no > %s
                ORDER BY seq_no ASC
                LIMIT %s
                """,
                (str(conversation_id), after_seq_no, limit),
            )
            rows = cursor.fetchall()

        return [
            ConversationEventRecord(
                seq_no=int(row[0]),
                event_type=row[1],
                actor_participant_id=row[2],
                message_id=row[3],
                payload=row[4],
                created_at=row[5],
            )
            for row in rows
        ]
