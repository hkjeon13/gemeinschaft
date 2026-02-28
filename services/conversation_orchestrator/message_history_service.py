"""Read-model service for conversation message history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError

_ALLOWED_MESSAGE_STATUSES = {"proposed", "validated", "rejected", "committed"}


@dataclass(frozen=True)
class ConversationMessageRecord:
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    status: str
    message_type: str
    content_text: str
    metadata: dict[str, Any]
    created_at: datetime


class MessageHistoryService:
    def __init__(self, connection: Any):
        self._connection = connection

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if after_turn_index < 0:
            raise ValueError("after_turn_index must be >= 0")
        normalized_status = status.strip().lower() if status else None
        if normalized_status and normalized_status not in _ALLOWED_MESSAGE_STATUSES:
            raise ValueError(f"unsupported status filter: {status}")

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

            if normalized_status:
                cursor.execute(
                    """
                    SELECT
                        m.turn_index,
                        m.id,
                        p.id,
                        p.display_name,
                        p.kind,
                        m.status,
                        m.message_type,
                        m.content_text,
                        m.metadata,
                        m.created_at
                    FROM message m
                    JOIN participant p ON m.participant_id = p.id
                    WHERE
                        m.conversation_id = %s
                        AND m.turn_index > %s
                        AND m.status = %s
                    ORDER BY m.turn_index ASC
                    LIMIT %s
                    """,
                    (str(conversation_id), after_turn_index, normalized_status, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                        m.turn_index,
                        m.id,
                        p.id,
                        p.display_name,
                        p.kind,
                        m.status,
                        m.message_type,
                        m.content_text,
                        m.metadata,
                        m.created_at
                    FROM message m
                    JOIN participant p ON m.participant_id = p.id
                    WHERE
                        m.conversation_id = %s
                        AND m.turn_index > %s
                    ORDER BY m.turn_index ASC
                    LIMIT %s
                    """,
                    (str(conversation_id), after_turn_index, limit),
                )
            rows = cursor.fetchall()

        return [
            ConversationMessageRecord(
                turn_index=int(row[0]),
                message_id=row[1],
                participant_id=row[2],
                participant_name=row[3],
                participant_kind=row[4],
                status=row[5],
                message_type=row[6],
                content_text=row[7],
                metadata=row[8],
                created_at=row[9],
            )
            for row in rows
        ]
