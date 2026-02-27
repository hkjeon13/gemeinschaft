"""Human intervention service for pause/resume/terminate/steer actions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


class InvalidInterventionTypeError(ValueError):
    """Raised when an intervention_type is unsupported."""


class InvalidInterventionStateError(RuntimeError):
    """Raised when transition is invalid for current conversation status."""


_VALID_INTERVENTION_TYPES = {"interrupt", "steer", "resume", "terminate"}

_STATUS_EVENT_BY_INTERVENTION = {
    "interrupt": "conversation.paused",
    "resume": "conversation.resumed",
    "terminate": "conversation.terminated",
}

_TARGET_STATUS_BY_INTERVENTION = {
    "interrupt": "paused",
    "resume": "active",
    "terminate": "completed",
}


@dataclass(frozen=True)
class ApplyInterventionInput:
    conversation_id: UUID
    intervention_type: str
    actor_participant_id: UUID | None
    instruction: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ApplyInterventionResult:
    conversation_id: UUID
    status: str
    event_seq_last: int
    applied_events: list[str]
    occurred_at: datetime


class HumanInterventionService:
    def __init__(self, connection: Any):
        self._connection = connection

    def apply_intervention(self, payload: ApplyInterventionInput) -> ApplyInterventionResult:
        intervention_type = payload.intervention_type.strip().lower()
        if intervention_type not in _VALID_INTERVENTION_TYPES:
            raise InvalidInterventionTypeError(
                f"Unsupported intervention_type: {payload.intervention_type}"
            )

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, status
                    FROM conversation
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (str(payload.conversation_id),),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ConversationNotFoundError(
                        f"Conversation {payload.conversation_id} not found"
                    )
                current_status = str(row[1])
                self._assert_transition(current_status, intervention_type)

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                event_seq_last = int(cursor.fetchone()[0])

                base_payload = {
                    "intervention_type": intervention_type,
                    "instruction": payload.instruction,
                    "metadata": payload.metadata,
                }
                event_seq_last += 1
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        actor_participant_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, %s, %s, 'human.intervention', %s::jsonb)
                    RETURNING created_at
                    """,
                    (
                        str(payload.conversation_id),
                        str(payload.actor_participant_id)
                        if payload.actor_participant_id
                        else None,
                        event_seq_last,
                        json.dumps(base_payload),
                    ),
                )
                created_row = cursor.fetchone()
                if created_row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("human.intervention insert did not return created_at")
                occurred_at = created_row[0]
                applied_events = ["human.intervention"]

                status_event = _STATUS_EVENT_BY_INTERVENTION.get(intervention_type)
                if status_event is not None:
                    event_seq_last += 1
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
                            status_event,
                            json.dumps(
                                {
                                    "intervention_type": intervention_type,
                                    "reason": "human_intervention",
                                }
                            ),
                        ),
                    )
                    status_row = cursor.fetchone()
                    if status_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError(f"{status_event} insert did not return created_at")
                    occurred_at = status_row[0]
                    applied_events.append(status_event)

                status = _TARGET_STATUS_BY_INTERVENTION.get(intervention_type, current_status)
                if status == "completed":
                    cursor.execute(
                        """
                        UPDATE conversation
                        SET
                            updated_at = NOW(),
                            ended_at = COALESCE(ended_at, NOW()),
                            status = %s
                        WHERE id = %s
                        """,
                        (status, str(payload.conversation_id)),
                    )
                elif status != current_status:
                    cursor.execute(
                        """
                        UPDATE conversation
                        SET
                            updated_at = NOW(),
                            status = %s
                        WHERE id = %s
                        """,
                        (status, str(payload.conversation_id)),
                    )
                else:
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

        return ApplyInterventionResult(
            conversation_id=payload.conversation_id,
            status=status,
            event_seq_last=event_seq_last,
            applied_events=applied_events,
            occurred_at=occurred_at,
        )

    def _assert_transition(self, current_status: str, intervention_type: str) -> None:
        allowed_statuses = {
            "interrupt": {"active"},
            "steer": {"active", "paused"},
            "resume": {"paused"},
            "terminate": {"active", "paused"},
        }[intervention_type]
        if current_status not in allowed_statuses:
            raise InvalidInterventionStateError(
                f"Cannot apply {intervention_type} when conversation status is {current_status}"
            )
