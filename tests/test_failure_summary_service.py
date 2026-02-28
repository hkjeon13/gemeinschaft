"""Unit tests for conversation failure summary service."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.failure_summary_service import (
    ConversationFailureSummaryService,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        message_counts: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
        event_counts: tuple[int, int] = (0, 0),
    ):
        self.conversation_exists = conversation_exists
        self.message_counts = message_counts
        self.event_counts = event_counts
        self._last_fetchone: Any = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id from conversation where id = %s" in normalized_sql:
            self._last_fetchone = ("conversation",) if self.conversation_exists else None
            return
        if "count(*) filter (where status = 'rejected')" in normalized_sql:
            self._last_fetchone = self.message_counts
            return
        if "count(*) filter ( where event_type = 'loop.guard_triggered' )" in normalized_sql:
            self._last_fetchone = self.event_counts
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone


def test_failure_summary_success() -> None:
    conversation_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        message_counts=(7, 3, 1, 2, 1),
        event_counts=(2, 1),
    )
    service = ConversationFailureSummaryService(connection)

    summary = service.get_summary(conversation_id=conversation_id)

    assert summary.conversation_id == conversation_id
    assert summary.rejected_turns == 7
    assert summary.missing_citation_count == 3
    assert summary.invalid_citation_count == 1
    assert summary.loop_risk_repetition_count == 2
    assert summary.topic_derailment_count == 1
    assert summary.loop_guard_trigger_count == 2
    assert summary.arbitration_requested_count == 1


def test_failure_summary_conversation_missing() -> None:
    connection = FakeConnection(conversation_exists=False)
    service = ConversationFailureSummaryService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.get_summary(conversation_id=uuid4())
