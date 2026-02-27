"""Tests for append-only event storage with optimistic sequence checks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import (
    AppendEventInput,
    ConversationNotFoundError,
    EventStore,
    SequenceConflictError,
)


class FakeConnection:
    def __init__(self, *, conversation_exists: bool, current_seq_no: int):
        self.conversation_exists = conversation_exists
        self.current_seq_no = current_seq_no
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executed_sql: list[str] = []
        self._last_fetch: Any = None
        self._next_event_id = 1

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        self.executed_sql.append(normalized_sql)
        if "select id from conversation" in normalized_sql:
            self._last_fetch = ("conversation-id",) if self.conversation_exists else None
            return
        if "select coalesce(max(seq_no), 0) from event" in normalized_sql:
            self._last_fetch = (self.current_seq_no,)
            return
        if "insert into event" in normalized_sql:
            assert params is not None
            next_seq_no = int(params[3])
            self.current_seq_no = next_seq_no
            self._last_fetch = (
                self._next_event_id,
                next_seq_no,
                datetime(2026, 2, 27, tzinfo=timezone.utc),
            )
            self._next_event_id += 1
            return
        raise AssertionError(f"Unexpected SQL in test fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetch

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def test_append_event_successful_commit() -> None:
    connection = FakeConnection(conversation_exists=True, current_seq_no=0)
    store = EventStore(connection)

    result = store.append_event(
        AppendEventInput(
            conversation_id=uuid4(),
            event_type="conversation.started",
            expected_seq_no=0,
            payload={"trigger": "automation"},
        )
    )

    assert result.event_id == 1
    assert result.seq_no == 1
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_append_event_rejects_missing_conversation() -> None:
    connection = FakeConnection(conversation_exists=False, current_seq_no=0)
    store = EventStore(connection)

    with pytest.raises(ConversationNotFoundError):
        store.append_event(
            AppendEventInput(
                conversation_id=uuid4(),
                event_type="conversation.started",
                expected_seq_no=0,
                payload={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_append_event_rejects_sequence_conflict() -> None:
    connection = FakeConnection(conversation_exists=True, current_seq_no=3)
    store = EventStore(connection)

    with pytest.raises(SequenceConflictError) as exc_info:
        store.append_event(
            AppendEventInput(
                conversation_id=uuid4(),
                event_type="turn.committed",
                expected_seq_no=2,
                payload={},
            )
        )

    assert exc_info.value.expected_seq_no == 2
    assert exc_info.value.actual_seq_no == 3
    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_append_event_requires_exact_expected_seq_no() -> None:
    connection = FakeConnection(conversation_exists=True, current_seq_no=0)
    store = EventStore(connection)
    conversation_id = uuid4()

    first = store.append_event(
        AppendEventInput(
            conversation_id=conversation_id,
            event_type="conversation.started",
            expected_seq_no=0,
            payload={},
        )
    )
    second = store.append_event(
        AppendEventInput(
            conversation_id=conversation_id,
            event_type="turn.committed",
            expected_seq_no=first.seq_no,
            payload={"turn_index": 1},
        )
    )

    assert first.seq_no == 1
    assert second.seq_no == 2
    assert connection.commit_calls == 2
    assert connection.rollback_calls == 0
