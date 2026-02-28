"""Read-model service for pending turn approvals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class PendingTurnRecord:
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    content_text: str
    created_at: datetime
    metadata: dict[str, Any]


class PendingTurnService:
    def __init__(self, connection: Any):
        self._connection = connection

    def list_pending_turns(
        self,
        *,
        conversation_id: UUID,
        limit: int = 20,
        after_turn_index: int = 0,
    ) -> list[PendingTurnRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if after_turn_index < 0:
            raise ValueError("after_turn_index must be >= 0")
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
                raise ConversationNotFoundError(
                    f"Conversation {conversation_id} not found"
                )
            cursor.execute(
                """
                SELECT
                    m.turn_index,
                    m.id,
                    p.id,
                    p.display_name,
                    p.kind,
                    m.content_text,
                    m.created_at,
                    m.metadata
                FROM message m
                JOIN participant p ON m.participant_id = p.id
                WHERE m.conversation_id = %s AND m.status = 'proposed' AND m.turn_index > %s
                ORDER BY m.turn_index ASC
                LIMIT %s
                """,
                (str(conversation_id), after_turn_index, limit),
            )
            rows = cursor.fetchall()

        return [
            PendingTurnRecord(
                turn_index=int(row[0]),
                message_id=row[1],
                participant_id=row[2],
                participant_name=row[3],
                participant_kind=row[4],
                content_text=row[5],
                created_at=row[6],
                metadata=row[7],
            )
            for row in rows
        ]
