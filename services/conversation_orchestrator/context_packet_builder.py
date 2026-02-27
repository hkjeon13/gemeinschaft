"""Context packet assembler for orchestrator turn planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


class TopicNotFoundError(RuntimeError):
    """Raised when requested topic is not found for a source."""


@dataclass(frozen=True)
class ContextPacketInput:
    conversation_id: UUID
    source_document_id: UUID
    topic_id: UUID | None
    turn_window: int
    evidence_limit: int


@dataclass(frozen=True)
class ContextTurn:
    turn_index: int
    speaker: str
    content_text: str


@dataclass(frozen=True)
class ContextEvidence:
    source_chunk_id: UUID
    chunk_index: int
    content_text: str
    relevance_score: float


@dataclass(frozen=True)
class ContextPacketResult:
    conversation_id: UUID
    source_document_id: UUID
    topic_id: UUID | None
    topic_label: str | None
    topic_summary: str | None
    recent_turns: list[ContextTurn]
    evidence_chunks: list[ContextEvidence]


class ContextPacketBuilder:
    def __init__(self, connection: Any):
        self._connection = connection

    def build_packet(self, payload: ContextPacketInput) -> ContextPacketResult:
        if payload.turn_window < 1:
            raise ValueError("turn_window must be >= 1")
        if payload.evidence_limit < 1:
            raise ValueError("evidence_limit must be >= 1")

        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM conversation WHERE id = %s",
                (str(payload.conversation_id),),
            )
            if cursor.fetchone() is None:
                raise ConversationNotFoundError(
                    f"Conversation {payload.conversation_id} not found"
                )

            topic_row = self._resolve_topic(cursor, payload)
            topic_id = topic_row[0] if topic_row is not None else None
            topic_label = topic_row[1] if topic_row is not None else None
            topic_summary = topic_row[2] if topic_row is not None else None

            cursor.execute(
                """
                SELECT m.turn_index, p.display_name, m.content_text
                FROM message m
                JOIN participant p ON m.participant_id = p.id
                WHERE m.conversation_id = %s
                ORDER BY m.turn_index DESC
                LIMIT %s
                """,
                (str(payload.conversation_id), payload.turn_window),
            )
            turns_desc = cursor.fetchall()
            recent_turns = [
                ContextTurn(turn_index=int(row[0]), speaker=row[1], content_text=row[2])
                for row in reversed(turns_desc)
            ]

            evidence_chunks: list[ContextEvidence] = []
            if topic_id is not None:
                cursor.execute(
                    """
                    SELECT sct.source_chunk_id, sc.chunk_index, sc.content_text, sct.relevance_score
                    FROM source_chunk_topic sct
                    JOIN source_chunk sc ON sct.source_chunk_id = sc.id
                    WHERE sct.topic_id = %s
                    ORDER BY sct.relevance_score DESC, sc.chunk_index ASC
                    LIMIT %s
                    """,
                    (str(topic_id), payload.evidence_limit),
                )
                evidence_chunks = [
                    ContextEvidence(
                        source_chunk_id=row[0],
                        chunk_index=int(row[1]),
                        content_text=row[2],
                        relevance_score=float(row[3]),
                    )
                    for row in cursor.fetchall()
                ]

        return ContextPacketResult(
            conversation_id=payload.conversation_id,
            source_document_id=payload.source_document_id,
            topic_id=topic_id,
            topic_label=topic_label,
            topic_summary=topic_summary,
            recent_turns=recent_turns,
            evidence_chunks=evidence_chunks,
        )

    def _resolve_topic(
        self, cursor: Any, payload: ContextPacketInput
    ) -> tuple[UUID, str, str] | None:
        if payload.topic_id is not None:
            cursor.execute(
                """
                SELECT id, label, summary
                FROM topic
                WHERE id = %s AND source_document_id = %s
                """,
                (str(payload.topic_id), str(payload.source_document_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise TopicNotFoundError(
                    f"Topic {payload.topic_id} not found for source {payload.source_document_id}"
                )
            return row

        cursor.execute(
            """
            SELECT id, label, summary
            FROM topic
            WHERE source_document_id = %s
            ORDER BY chunk_count DESC, created_at ASC
            LIMIT 1
            """,
            (str(payload.source_document_id),),
        )
        row = cursor.fetchone()
        return row
