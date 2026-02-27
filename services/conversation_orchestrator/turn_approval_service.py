"""Turn approval workflow service for proposed AI turns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


class TurnNotFoundError(RuntimeError):
    """Raised when a turn does not exist for the target conversation."""


class InvalidApprovalDecisionError(ValueError):
    """Raised when approval decision is unsupported."""


class TurnApprovalStateError(RuntimeError):
    """Raised when target turn is not in approvable state."""


@dataclass(frozen=True)
class ApplyTurnApprovalInput:
    conversation_id: UUID
    turn_index: int
    decision: str
    actor_participant_id: UUID | None
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ApplyTurnApprovalResult:
    conversation_id: UUID
    turn_index: int
    message_status: str
    event_seq_last: int
    applied_events: list[str]
    occurred_at: datetime


class TurnApprovalService:
    def __init__(self, connection: Any):
        self._connection = connection

    def apply_decision(self, payload: ApplyTurnApprovalInput) -> ApplyTurnApprovalResult:
        decision = payload.decision.strip().lower()
        if decision not in {"approve", "reject"}:
            raise InvalidApprovalDecisionError(
                f"Unsupported decision: {payload.decision}"
            )

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
                    SELECT id, status, participant_id
                    FROM message
                    WHERE conversation_id = %s AND turn_index = %s
                    FOR UPDATE
                    """,
                    (str(payload.conversation_id), payload.turn_index),
                )
                row = cursor.fetchone()
                if row is None:
                    raise TurnNotFoundError(
                        f"Turn {payload.turn_index} not found in conversation "
                        f"{payload.conversation_id}"
                    )
                message_id = row[0]
                message_status = str(row[1])
                message_participant_id = row[2]
                if message_status != "proposed":
                    raise TurnApprovalStateError(
                        f"Turn {payload.turn_index} is not proposed (status={message_status})"
                    )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                event_seq_last = int(cursor.fetchone()[0])

                applied_events: list[str] = []
                if decision == "approve":
                    cursor.execute(
                        """
                        UPDATE message
                        SET status = 'committed'
                        WHERE id = %s
                        """,
                        (str(message_id),),
                    )
                    event_seq_last += 1
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
                        VALUES (%s, %s, %s, %s, 'turn.approved', %s::jsonb)
                        RETURNING created_at
                        """,
                        (
                            str(payload.conversation_id),
                            str(message_id),
                            str(payload.actor_participant_id)
                            if payload.actor_participant_id
                            else None,
                            event_seq_last,
                            json.dumps(
                                {
                                    "turn_index": payload.turn_index,
                                    "decision": "approve",
                                    "reason": payload.reason,
                                    "metadata": payload.metadata,
                                }
                            ),
                        ),
                    )
                    approved_row = cursor.fetchone()
                    if approved_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("turn.approved insert did not return created_at")
                    occurred_at = approved_row[0]
                    applied_events.append("turn.approved")

                    event_seq_last += 1
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
                        VALUES (%s, %s, %s, %s, 'turn.committed', %s::jsonb)
                        RETURNING created_at
                        """,
                        (
                            str(payload.conversation_id),
                            str(message_id),
                            str(message_participant_id) if message_participant_id else None,
                            event_seq_last,
                            json.dumps(
                                {
                                    "turn_index": payload.turn_index,
                                    "approved_by": str(payload.actor_participant_id)
                                    if payload.actor_participant_id
                                    else None,
                                }
                            ),
                        ),
                    )
                    committed_row = cursor.fetchone()
                    if committed_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("turn.committed insert did not return created_at")
                    occurred_at = committed_row[0]
                    applied_events.append("turn.committed")
                    final_status = "committed"
                else:
                    cursor.execute(
                        """
                        UPDATE message
                        SET status = 'rejected'
                        WHERE id = %s
                        """,
                        (str(message_id),),
                    )
                    event_seq_last += 1
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
                        VALUES (%s, %s, %s, %s, 'turn.rejected', %s::jsonb)
                        RETURNING created_at
                        """,
                        (
                            str(payload.conversation_id),
                            str(message_id),
                            str(payload.actor_participant_id)
                            if payload.actor_participant_id
                            else None,
                            event_seq_last,
                            json.dumps(
                                {
                                    "turn_index": payload.turn_index,
                                    "decision": "reject",
                                    "reason": payload.reason,
                                    "metadata": payload.metadata,
                                }
                            ),
                        ),
                    )
                    rejected_row = cursor.fetchone()
                    if rejected_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("turn.rejected insert did not return created_at")
                    occurred_at = rejected_row[0]
                    applied_events.append("turn.rejected")
                    final_status = "rejected"
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return ApplyTurnApprovalResult(
            conversation_id=payload.conversation_id,
            turn_index=payload.turn_index,
            message_status=final_status,
            event_seq_last=event_seq_last,
            applied_events=applied_events,
            occurred_at=occurred_at,
        )
