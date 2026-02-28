"""Operational summary read service for a conversation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class ConversationOpsSummary:
    conversation_id: UUID
    status: str
    title: str
    objective: str | None
    updated_at: datetime
    participant_count: int
    total_messages: int
    committed_messages: int
    proposed_messages: int
    rejected_messages: int
    validated_messages: int
    last_event_seq_no: int
    last_event_type: str | None
    last_event_at: datetime | None


class ConversationOpsSummaryService:
    def __init__(self, connection: Any):
        self._connection = connection

    def get_summary(self, *, conversation_id: UUID) -> ConversationOpsSummary:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, title, objective, updated_at
                FROM conversation
                WHERE id = %s
                """,
                (str(conversation_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise ConversationNotFoundError(
                    f"Conversation {conversation_id} not found"
                )

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM participant
                WHERE conversation_id = %s
                """,
                (str(conversation_id),),
            )
            participant_count = int(cursor.fetchone()[0])

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_messages,
                    COUNT(*) FILTER (WHERE status = 'committed') AS committed_messages,
                    COUNT(*) FILTER (WHERE status = 'proposed') AS proposed_messages,
                    COUNT(*) FILTER (WHERE status = 'rejected') AS rejected_messages,
                    COUNT(*) FILTER (WHERE status = 'validated') AS validated_messages
                FROM message
                WHERE conversation_id = %s
                """,
                (str(conversation_id),),
            )
            message_counts = cursor.fetchone()

            cursor.execute(
                """
                SELECT seq_no, event_type, created_at
                FROM event
                WHERE conversation_id = %s
                ORDER BY seq_no DESC
                LIMIT 1
                """,
                (str(conversation_id),),
            )
            last_event = cursor.fetchone()

        return ConversationOpsSummary(
            conversation_id=row[0],
            status=row[1],
            title=row[2],
            objective=row[3],
            updated_at=row[4],
            participant_count=participant_count,
            total_messages=int(message_counts[0]),
            committed_messages=int(message_counts[1]),
            proposed_messages=int(message_counts[2]),
            rejected_messages=int(message_counts[3]),
            validated_messages=int(message_counts[4]),
            last_event_seq_no=int(last_event[0]) if last_event else 0,
            last_event_type=str(last_event[1]) if last_event else None,
            last_event_at=last_event[2] if last_event else None,
        )
