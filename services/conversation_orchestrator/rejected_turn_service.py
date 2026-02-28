"""Read-model service for rejected turns review queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class RejectedTurnRecord:
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    content_text: str
    failure_type: str | None
    reasons: list[str]
    created_at: datetime
    metadata: dict[str, Any]


class RejectedTurnService:
    def __init__(self, connection: Any):
        self._connection = connection

    def list_rejected_turns(
        self,
        *,
        conversation_id: UUID,
        limit: int = 20,
        before_turn_index: int | None = None,
    ) -> list[RejectedTurnRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if before_turn_index is not None and before_turn_index < 1:
            raise ValueError("before_turn_index must be >= 1")

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

            if before_turn_index is None:
                cursor.execute(
                    """
                    SELECT
                        m.turn_index,
                        m.id,
                        p.id,
                        p.display_name,
                        p.kind,
                        m.content_text,
                        m.metadata -> 'validation' ->> 'failure_type' AS failure_type,
                        COALESCE(m.metadata -> 'validation' -> 'reasons', '[]'::jsonb) AS reasons,
                        m.created_at,
                        m.metadata
                    FROM message m
                    JOIN participant p ON m.participant_id = p.id
                    WHERE m.conversation_id = %s AND m.status = 'rejected'
                    ORDER BY m.turn_index DESC
                    LIMIT %s
                    """,
                    (str(conversation_id), limit),
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
                        m.content_text,
                        m.metadata -> 'validation' ->> 'failure_type' AS failure_type,
                        COALESCE(m.metadata -> 'validation' -> 'reasons', '[]'::jsonb) AS reasons,
                        m.created_at,
                        m.metadata
                    FROM message m
                    JOIN participant p ON m.participant_id = p.id
                    WHERE
                        m.conversation_id = %s
                        AND m.status = 'rejected'
                        AND m.turn_index < %s
                    ORDER BY m.turn_index DESC
                    LIMIT %s
                    """,
                    (str(conversation_id), before_turn_index, limit),
                )
            rows = cursor.fetchall()

        return [
            RejectedTurnRecord(
                turn_index=int(row[0]),
                message_id=row[1],
                participant_id=row[2],
                participant_name=row[3],
                participant_kind=row[4],
                content_text=row[5],
                failure_type=row[6],
                reasons=list(row[7]) if isinstance(row[7], list) else [],
                created_at=row[8],
                metadata=row[9],
            )
            for row in rows
        ]
