"""Append-only conversation event store with optimistic sequence checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


class ConversationNotFoundError(RuntimeError):
    """Raised when appending to a conversation that does not exist."""


class SequenceConflictError(RuntimeError):
    """Raised when expected sequence does not match current sequence."""

    def __init__(self, expected_seq_no: int, actual_seq_no: int):
        super().__init__(
            f"Expected seq_no {expected_seq_no}, but current seq_no is {actual_seq_no}"
        )
        self.expected_seq_no = expected_seq_no
        self.actual_seq_no = actual_seq_no


@dataclass(frozen=True)
class AppendEventInput:
    conversation_id: UUID
    event_type: str
    expected_seq_no: int
    payload: dict[str, Any]
    message_id: UUID | None = None
    actor_participant_id: UUID | None = None


@dataclass(frozen=True)
class AppendEventResult:
    event_id: int
    seq_no: int
    created_at: datetime


class EventStore:
    """Small repository around the `event` table append path."""

    def __init__(self, connection: Any):
        self._connection = connection

    def append_event(self, request: AppendEventInput) -> AppendEventResult:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM conversation
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (str(request.conversation_id),),
                )
                if cursor.fetchone() is None:
                    raise ConversationNotFoundError(
                        f"Conversation {request.conversation_id} not found"
                    )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(request.conversation_id),),
                )
                current_seq_no = int(cursor.fetchone()[0])

                if current_seq_no != request.expected_seq_no:
                    raise SequenceConflictError(
                        expected_seq_no=request.expected_seq_no,
                        actual_seq_no=current_seq_no,
                    )

                next_seq_no = current_seq_no + 1
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        message_id,
                        actor_participant_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id, seq_no, created_at
                    """,
                    (
                        str(request.conversation_id),
                        str(request.message_id) if request.message_id else None,
                        str(request.actor_participant_id)
                        if request.actor_participant_id
                        else None,
                        next_seq_no,
                        request.event_type,
                        json.dumps(request.payload),
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("Event insert did not return a row")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return AppendEventResult(
            event_id=int(row[0]),
            seq_no=int(row[1]),
            created_at=row[2],
        )
