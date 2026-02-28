"""Participant moderation service (mute/unmute)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


class ParticipantModerationNotFoundError(RuntimeError):
    """Raised when participant is not found in target conversation."""


class InvalidModerationActionError(ValueError):
    """Raised when moderation action is unsupported."""


class ParticipantModerationStateError(RuntimeError):
    """Raised when moderation action is not applicable for current state."""


@dataclass(frozen=True)
class ApplyParticipantModerationInput:
    conversation_id: UUID
    participant_id: UUID
    action: str
    actor_participant_id: UUID | None
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ApplyParticipantModerationResult:
    conversation_id: UUID
    participant_id: UUID
    muted: bool
    event_type: str
    event_seq_last: int
    occurred_at: datetime


class ParticipantModerationService:
    def __init__(self, connection: Any):
        self._connection = connection

    def apply(self, payload: ApplyParticipantModerationInput) -> ApplyParticipantModerationResult:
        action = payload.action.strip().lower()
        if action not in {"mute", "unmute"}:
            raise InvalidModerationActionError(f"Unsupported action: {payload.action}")

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
                    SELECT id, metadata
                    FROM participant
                    WHERE id = %s AND conversation_id = %s
                    FOR UPDATE
                    """,
                    (str(payload.participant_id), str(payload.conversation_id)),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ParticipantModerationNotFoundError(
                        f"Participant {payload.participant_id} not found "
                        f"in conversation {payload.conversation_id}"
                    )

                participant_metadata = row[1] if isinstance(row[1], dict) else {}
                moderation = (
                    participant_metadata.get("moderation", {})
                    if isinstance(participant_metadata.get("moderation"), dict)
                    else {}
                )
                is_muted = moderation.get("muted") is True
                if action == "mute":
                    if is_muted:
                        raise ParticipantModerationStateError(
                            f"Participant {payload.participant_id} is already muted"
                        )
                    muted = True
                    event_type = "participant.muted"
                else:
                    if not is_muted:
                        raise ParticipantModerationStateError(
                            f"Participant {payload.participant_id} is not muted"
                        )
                    muted = False
                    event_type = "participant.unmuted"

                moderation.update(
                    {
                        "muted": muted,
                        "reason": payload.reason,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                participant_metadata["moderation"] = moderation

                cursor.execute(
                    """
                    UPDATE participant
                    SET metadata = %s::jsonb
                    WHERE id = %s
                    """,
                    (json.dumps(participant_metadata), str(payload.participant_id)),
                )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                event_seq_last = int(cursor.fetchone()[0]) + 1
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        actor_participant_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    RETURNING created_at
                    """,
                    (
                        str(payload.conversation_id),
                        str(payload.actor_participant_id)
                        if payload.actor_participant_id
                        else None,
                        event_seq_last,
                        event_type,
                        json.dumps(
                            {
                                "participant_id": str(payload.participant_id),
                                "action": action,
                                "muted": muted,
                                "reason": payload.reason,
                                "metadata": payload.metadata,
                            }
                        ),
                    ),
                )
                event_row = cursor.fetchone()
                if event_row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError(f"{event_type} insert did not return created_at")
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

        return ApplyParticipantModerationResult(
            conversation_id=payload.conversation_id,
            participant_id=payload.participant_id,
            muted=muted,
            event_type=event_type,
            event_seq_last=event_seq_last,
            occurred_at=occurred_at,
        )
