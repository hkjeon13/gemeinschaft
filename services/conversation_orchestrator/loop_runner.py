"""Conversation loop runner (v1): deterministic round-robin with validation guard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.turn_validator import (
    TurnValidationInput,
    TurnValidator,
)


class NoParticipantsError(RuntimeError):
    """Raised when a conversation has no participants to run turns for."""


@dataclass(frozen=True)
class ParticipantRecord:
    id: UUID
    kind: str
    display_name: str


@dataclass(frozen=True)
class RunLoopInput:
    conversation_id: UUID
    max_turns: int
    require_citations: bool = False
    required_citation_ids: list[UUID] | None = None


@dataclass(frozen=True)
class RunLoopResult:
    conversation_id: UUID
    turns_created: int
    turns_rejected: int
    event_seq_last: int
    turn_index_last: int
    started_at: datetime
    finished_at: datetime


class ConversationLoopRunner:
    def __init__(self, connection: Any, validator: TurnValidator | None = None):
        self._connection = connection
        self._validator = validator or TurnValidator()

    def run_loop(self, payload: RunLoopInput) -> RunLoopResult:
        if payload.max_turns < 1:
            raise ValueError("max_turns must be >= 1")

        started_at = datetime.now(timezone.utc)
        allowed_citation_ids = {
            str(citation_id).lower() for citation_id in (payload.required_citation_ids or [])
        }
        turns_created = 0
        turns_rejected = 0
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
                    SELECT id, kind, display_name
                    FROM participant
                    WHERE conversation_id = %s
                    ORDER BY joined_at ASC, id ASC
                    """,
                    (str(payload.conversation_id),),
                )
                participants = [
                    ParticipantRecord(id=row[0], kind=row[1], display_name=row[2])
                    for row in cursor.fetchall()
                ]
                if not participants:
                    raise NoParticipantsError(
                        f"Conversation {payload.conversation_id} has no participants"
                    )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), 0)
                    FROM message
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                turn_index_last = int(cursor.fetchone()[0])

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                event_seq_last = int(cursor.fetchone()[0])

                cursor.execute(
                    """
                    SELECT content_text
                    FROM message
                    WHERE conversation_id = %s
                    ORDER BY turn_index DESC
                    LIMIT 5
                    """,
                    (str(payload.conversation_id),),
                )
                recent_turn_texts = [row[0] for row in cursor.fetchall()]

                for _ in range(payload.max_turns):
                    turn_index_last += 1
                    participant = participants[(turn_index_last - 1) % len(participants)]
                    content_text = self._build_turn_content(
                        turn_index=turn_index_last,
                        participant=participant,
                        required_citation_ids=payload.required_citation_ids or [],
                    )
                    validation = self._validator.validate(
                        TurnValidationInput(
                            participant_kind=participant.kind,
                            content_text=content_text,
                            require_citations=payload.require_citations,
                            allowed_citation_ids=allowed_citation_ids,
                            recent_turn_texts=recent_turn_texts,
                        )
                    )
                    message_status = "committed" if validation.is_valid else "rejected"
                    cursor.execute(
                        """
                        INSERT INTO message (
                            conversation_id,
                            participant_id,
                            turn_index,
                            message_type,
                            status,
                            content_text,
                            metadata
                        )
                        VALUES (%s, %s, %s, 'statement', %s, %s, %s::jsonb)
                        RETURNING id
                        """,
                        (
                            str(payload.conversation_id),
                            str(participant.id),
                            turn_index_last,
                            message_status,
                            content_text,
                            json.dumps(
                                {
                                    "loop_runner": "v1",
                                    "kind": participant.kind,
                                    "validation": {
                                        "is_valid": validation.is_valid,
                                        "failure_type": validation.failure_type,
                                        "reasons": validation.reasons,
                                        "citations": validation.citations,
                                    },
                                }
                            ),
                        ),
                    )
                    message_row = cursor.fetchone()
                    if message_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("Message insert did not return id")
                    message_id = message_row[0]

                    event_seq_last += 1
                    event_type = "turn.committed" if validation.is_valid else "turn.rejected"
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
                        """,
                        (
                            str(payload.conversation_id),
                            str(message_id),
                            str(participant.id),
                            event_seq_last,
                            event_type,
                            json.dumps(
                                {
                                    "turn_index": turn_index_last,
                                    "participant_id": str(participant.id),
                                    "validation": {
                                        "is_valid": validation.is_valid,
                                        "failure_type": validation.failure_type,
                                    },
                                }
                            ),
                        ),
                    )
                    if validation.is_valid:
                        turns_created += 1
                    else:
                        turns_rejected += 1
                    recent_turn_texts = [content_text, *recent_turn_texts[:4]]

                final_status = "paused" if turns_created == 0 and turns_rejected > 0 else "active"
                cursor.execute(
                    """
                    UPDATE conversation
                    SET
                        updated_at = NOW(),
                        status = %s
                    WHERE id = %s
                    """,
                    (final_status, str(payload.conversation_id)),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        finished_at = datetime.now(timezone.utc)
        return RunLoopResult(
            conversation_id=payload.conversation_id,
            turns_created=turns_created,
            turns_rejected=turns_rejected,
            event_seq_last=event_seq_last,
            turn_index_last=turn_index_last,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _build_turn_content(
        self,
        *,
        turn_index: int,
        participant: ParticipantRecord,
        required_citation_ids: list[UUID],
    ) -> str:
        content_text = f"[loop-v1] turn {turn_index} by {participant.display_name}"
        if participant.kind == "ai" and required_citation_ids:
            content_text += f" [chunk:{required_citation_ids[0]}]"
        return content_text
