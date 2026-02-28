"""Operational failure summary service for a conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class ConversationFailureSummary:
    conversation_id: UUID
    rejected_turns: int
    missing_citation_count: int
    invalid_citation_count: int
    loop_risk_repetition_count: int
    topic_derailment_count: int
    loop_guard_trigger_count: int
    arbitration_requested_count: int


class ConversationFailureSummaryService:
    def __init__(self, connection: Any):
        self._connection = connection

    def get_summary(self, *, conversation_id: UUID) -> ConversationFailureSummary:
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

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'rejected') AS rejected_turns,
                    COUNT(*) FILTER (
                        WHERE
                            status = 'rejected'
                            AND metadata -> 'validation' ->> 'failure_type' = 'missing_citation'
                    ) AS missing_citation_count,
                    COUNT(*) FILTER (
                        WHERE
                            status = 'rejected'
                            AND metadata -> 'validation' ->> 'failure_type' = 'invalid_citation'
                    ) AS invalid_citation_count,
                    COUNT(*) FILTER (
                        WHERE
                            status = 'rejected'
                            AND metadata -> 'validation' ->> 'failure_type' = 'loop_risk_repetition'
                    ) AS loop_risk_repetition_count,
                    COUNT(*) FILTER (
                        WHERE
                            status = 'rejected'
                            AND metadata -> 'validation' ->> 'failure_type' = 'topic_derailment'
                    ) AS topic_derailment_count
                FROM message
                WHERE conversation_id = %s
                """,
                (str(conversation_id),),
            )
            message_counts = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE event_type = 'loop.guard_triggered'
                    ) AS loop_guard_trigger_count,
                    COUNT(*) FILTER (
                        WHERE event_type = 'turn.arbitration_requested'
                    ) AS arbitration_requested_count
                FROM event
                WHERE conversation_id = %s
                """,
                (str(conversation_id),),
            )
            event_counts = cursor.fetchone()

        return ConversationFailureSummary(
            conversation_id=conversation_id,
            rejected_turns=int(message_counts[0]),
            missing_citation_count=int(message_counts[1]),
            invalid_citation_count=int(message_counts[2]),
            loop_risk_repetition_count=int(message_counts[3]),
            topic_derailment_count=int(message_counts[4]),
            loop_guard_trigger_count=int(event_counts[0]),
            arbitration_requested_count=int(event_counts[1]),
        )
