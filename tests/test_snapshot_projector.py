"""Tests for conversation snapshot projector and replay determinism."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.snapshot_projector import (
    ProjectableEvent,
    SnapshotProjector,
    project_snapshot,
)


def _dt(hour: int) -> datetime:
    return datetime(2026, 2, 27, hour, 0, tzinfo=timezone.utc)


class FakeSnapshotConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        event_rows: list[tuple[int, str, dict[str, Any], datetime]],
    ):
        self.conversation_exists = conversation_exists
        self.event_rows = event_rows
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self._last_fetchall: Any = []
        self.snapshot_upsert_params: tuple[Any, ...] | None = None

    def cursor(self) -> "FakeSnapshotConnection":
        return self

    def __enter__(self) -> "FakeSnapshotConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id from conversation" in normalized_sql:
            self._last_fetchone = ("conversation-id",) if self.conversation_exists else None
            return
        if "select seq_no, event_type, payload, created_at from event" in normalized_sql:
            self._last_fetchall = self.event_rows
            return
        if "insert into conversation_snapshot" in normalized_sql:
            assert params is not None
            self.snapshot_upsert_params = params
            return
        raise AssertionError(f"Unexpected SQL in test fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_project_snapshot_is_deterministic() -> None:
    conversation_id = uuid4()
    events = [
        ProjectableEvent(1, "conversation.created", {}, _dt(9)),
        ProjectableEvent(2, "conversation.started", {}, _dt(10)),
        ProjectableEvent(3, "turn.committed", {"turn_index": 1}, _dt(11)),
        ProjectableEvent(4, "turn.committed", {"turn_index": 2}, _dt(12)),
        ProjectableEvent(5, "conversation.paused", {}, _dt(13)),
        ProjectableEvent(6, "conversation.resumed", {}, _dt(14)),
        ProjectableEvent(7, "conversation.completed", {}, _dt(15)),
    ]

    first = project_snapshot(conversation_id=conversation_id, events=events)
    second = project_snapshot(conversation_id=conversation_id, events=events)

    assert first == second
    assert first.status == "completed"
    assert first.last_seq_no == 7
    assert first.turn_count == 2
    assert first.started_at == _dt(10)
    assert first.ended_at == _dt(15)
    assert first.last_event_at == _dt(15)


def test_rebuild_snapshot_persists_projected_state() -> None:
    conversation_id = uuid4()
    connection = FakeSnapshotConnection(
        conversation_exists=True,
        event_rows=[
            (1, "conversation.created", {}, _dt(9)),
            (2, "conversation.started", {}, _dt(10)),
            (3, "turn.committed", {"turn_index": 1}, _dt(11)),
            (4, "conversation.completed", {}, _dt(12)),
        ],
    )
    projector = SnapshotProjector(connection)

    snapshot = projector.rebuild_conversation_snapshot(conversation_id=conversation_id)

    assert snapshot.status == "completed"
    assert snapshot.last_seq_no == 4
    assert snapshot.turn_count == 1
    assert snapshot.started_at == _dt(10)
    assert snapshot.ended_at == _dt(12)
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0
    assert connection.snapshot_upsert_params is not None
    assert connection.snapshot_upsert_params[0] == str(conversation_id)
    assert connection.snapshot_upsert_params[1] == "completed"
    assert connection.snapshot_upsert_params[2] == 4
    assert connection.snapshot_upsert_params[3] == 1


def test_rebuild_snapshot_fails_for_missing_conversation() -> None:
    projector = SnapshotProjector(
        FakeSnapshotConnection(conversation_exists=False, event_rows=[])
    )

    with pytest.raises(ConversationNotFoundError):
        projector.rebuild_conversation_snapshot(conversation_id=uuid4())
