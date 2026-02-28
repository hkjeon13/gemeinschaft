"""Unit tests for conversation ops summary service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.ops_summary_service import (
    ConversationOpsSummaryService,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_row: tuple[Any, ...] | None,
        participant_count: int,
        message_counts: tuple[int, int, int, int, int],
        last_event_row: tuple[int, str, datetime] | None,
    ):
        self.conversation_row = conversation_row
        self.participant_count = participant_count
        self.message_counts = message_counts
        self.last_event_row = last_event_row
        self._last_fetchone: Any = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "from conversation where id = %s" in normalized_sql:
            self._last_fetchone = self.conversation_row
            return
        if "select count(*) from participant where conversation_id = %s" in normalized_sql:
            self._last_fetchone = (self.participant_count,)
            return
        if "count(*) as total_messages" in normalized_sql and "from message" in normalized_sql:
            self._last_fetchone = self.message_counts
            return
        if "select seq_no, event_type, created_at from event" in normalized_sql:
            self._last_fetchone = self.last_event_row
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone


def test_get_summary_success() -> None:
    ts = datetime(2026, 2, 28, 0, 40, tzinfo=timezone.utc)
    conversation_id = uuid4()
    connection = FakeConnection(
        conversation_row=(conversation_id, "active", "Ops", "check", ts),
        participant_count=3,
        message_counts=(11, 7, 2, 2, 0),
        last_event_row=(17, "turn.committed", ts),
    )
    service = ConversationOpsSummaryService(connection)

    summary = service.get_summary(conversation_id=conversation_id)

    assert summary.conversation_id == conversation_id
    assert summary.status == "active"
    assert summary.participant_count == 3
    assert summary.total_messages == 11
    assert summary.proposed_messages == 2
    assert summary.last_event_seq_no == 17
    assert summary.last_event_type == "turn.committed"


def test_get_summary_without_events() -> None:
    ts = datetime(2026, 2, 28, 0, 41, tzinfo=timezone.utc)
    conversation_id = uuid4()
    connection = FakeConnection(
        conversation_row=(conversation_id, "paused", "Ops", None, ts),
        participant_count=1,
        message_counts=(0, 0, 0, 0, 0),
        last_event_row=None,
    )
    service = ConversationOpsSummaryService(connection)

    summary = service.get_summary(conversation_id=conversation_id)

    assert summary.last_event_seq_no == 0
    assert summary.last_event_type is None
    assert summary.last_event_at is None


def test_get_summary_conversation_not_found() -> None:
    connection = FakeConnection(
        conversation_row=None,
        participant_count=0,
        message_counts=(0, 0, 0, 0, 0),
        last_event_row=None,
    )
    service = ConversationOpsSummaryService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.get_summary(conversation_id=uuid4())
