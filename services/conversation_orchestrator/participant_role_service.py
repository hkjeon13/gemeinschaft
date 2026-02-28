"""Participant role switching service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


class ParticipantNotFoundError(RuntimeError):
    """Raised when participant is not found in target conversation."""


@dataclass(frozen=True)
class SwitchParticipantRoleInput:
    conversation_id: UUID
    participant_id: UUID
    new_role_label: str
    actor_participant_id: UUID | None
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SwitchParticipantRoleResult:
    conversation_id: UUID
    participant_id: UUID
    previous_role_label: str | None
    new_role_label: str
    event_seq_last: int
    occurred_at: datetime


class ParticipantRoleService:
    def __init__(self, connection: Any):
        self._connection = connection

    def switch_role(self, payload: SwitchParticipantRoleInput) -> SwitchParticipantRoleResult:
        new_role_label = payload.new_role_label.strip()
        if not new_role_label:
            raise ValueError("new_role_label must not be empty")

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM conversation
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (str(payload.conversation_id),),
                )
                if cursor.fetchone() is None:
                    raise ConversationNotFoundError(
                        f"Conversation {payload.conversation_id} not found"
                    )

                cursor.execute(
                    """
                    SELECT id, role_label
                    FROM participant
                    WHERE id = %s AND conversation_id = %s
                    FOR UPDATE
                    """,
                    (str(payload.participant_id), str(payload.conversation_id)),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ParticipantNotFoundError(
                        f"Participant {payload.participant_id} not found "
                        f"in conversation {payload.conversation_id}"
                    )
                previous_role_label = row[1]
                if previous_role_label == new_role_label:
                    raise ValueError(
                        f"Participant already has role_label '{new_role_label}'"
                    )

                cursor.execute(
                    """
                    UPDATE participant
                    SET role_label = %s
                    WHERE id = %s
                    """,
                    (new_role_label, str(payload.participant_id)),
                )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                current_seq = int(cursor.fetchone()[0])
                next_seq = current_seq + 1
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        actor_participant_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, %s, %s, 'participant.role_switched', %s::jsonb)
                    RETURNING created_at
                    """,
                    (
                        str(payload.conversation_id),
                        str(payload.actor_participant_id)
                        if payload.actor_participant_id
                        else None,
                        next_seq,
                        json.dumps(
                            {
                                "participant_id": str(payload.participant_id),
                                "previous_role_label": previous_role_label,
                                "new_role_label": new_role_label,
                                "reason": payload.reason,
                                "metadata": payload.metadata,
                            }
                        ),
                    ),
                )
                event_row = cursor.fetchone()
                if event_row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("participant.role_switched insert did not return created_at")
                occurred_at = event_row[0]

                cursor.execute(
                    """
                    UPDATE conversation
                    SET updated_at = NOW()
                    WHERE id = %s
                    """,
                    (str(payload.conversation_id),),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return SwitchParticipantRoleResult(
            conversation_id=payload.conversation_id,
            participant_id=payload.participant_id,
            previous_role_label=previous_role_label,
            new_role_label=new_role_label,
            event_seq_last=next_seq,
            occurred_at=occurred_at,
        )
