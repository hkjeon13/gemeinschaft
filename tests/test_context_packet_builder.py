"""Unit tests for context packet builder."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.context_packet_builder import (
    ContextPacketBuilder,
    ContextPacketInput,
    TopicNotFoundError,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        topic_by_id: dict[str, tuple[Any, str, str]],
        default_topic_row: tuple[Any, str, str] | None,
        turns_desc: list[tuple[int, str, str]],
        evidence_rows: list[tuple[Any, int, str, float]],
    ):
        self.conversation_exists = conversation_exists
        self.topic_by_id = topic_by_id
        self.default_topic_row = default_topic_row
        self.turns_desc = turns_desc
        self.evidence_rows = evidence_rows
        self._last_fetchone: Any = None
        self._last_fetchall: Any = []

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id from conversation where id" in normalized_sql:
            self._last_fetchone = ("conversation",) if self.conversation_exists else None
            return
        if "from topic where id = %s and source_document_id = %s" in normalized_sql:
            assert params is not None
            self._last_fetchone = self.topic_by_id.get(str(params[0]))
            return
        if "from topic where source_document_id = %s" in normalized_sql:
            self._last_fetchone = self.default_topic_row
            return
        if "from message m join participant p" in normalized_sql:
            self._last_fetchall = self.turns_desc
            return
        if "from source_chunk_topic sct join source_chunk sc" in normalized_sql:
            self._last_fetchall = self.evidence_rows
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_context_packet_builder_assembles_topic_turns_and_evidence() -> None:
    conversation_id = uuid4()
    source_document_id = uuid4()
    topic_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        topic_by_id={},
        default_topic_row=(topic_id, "Refund", "Refund related summaries"),
        turns_desc=[
            (3, "AI(2)", "third turn"),
            (2, "AI(1)", "second turn"),
        ],
        evidence_rows=[
            (uuid4(), 4, "evidence text 1", 0.98),
            (uuid4(), 5, "evidence text 2", 0.92),
        ],
    )
    builder = ContextPacketBuilder(connection)

    result = builder.build_packet(
        ContextPacketInput(
            conversation_id=conversation_id,
            source_document_id=source_document_id,
            topic_id=None,
            turn_window=8,
            evidence_limit=5,
        )
    )

    assert result.conversation_id == conversation_id
    assert result.topic_id == topic_id
    assert result.topic_label == "Refund"
    assert [turn.turn_index for turn in result.recent_turns] == [2, 3]
    assert len(result.evidence_chunks) == 2


def test_context_packet_builder_raises_when_topic_not_found() -> None:
    builder = ContextPacketBuilder(
        FakeConnection(
            conversation_exists=True,
            topic_by_id={},
            default_topic_row=None,
            turns_desc=[],
            evidence_rows=[],
        )
    )

    with pytest.raises(TopicNotFoundError):
        builder.build_packet(
            ContextPacketInput(
                conversation_id=uuid4(),
                source_document_id=uuid4(),
                topic_id=uuid4(),
                turn_window=5,
                evidence_limit=3,
            )
        )


def test_context_packet_builder_raises_when_conversation_missing() -> None:
    builder = ContextPacketBuilder(
        FakeConnection(
            conversation_exists=False,
            topic_by_id={},
            default_topic_row=None,
            turns_desc=[],
            evidence_rows=[],
        )
    )

    with pytest.raises(ConversationNotFoundError):
        builder.build_packet(
            ContextPacketInput(
                conversation_id=uuid4(),
                source_document_id=uuid4(),
                topic_id=None,
                turn_window=5,
                evidence_limit=3,
            )
        )
