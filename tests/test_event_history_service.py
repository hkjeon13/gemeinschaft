"""Unit tests for conversation event history service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_history_service import EventHistoryService
from services.conversation_orchestrator.event_store import ConversationNotFoundError


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        event_rows: list[tuple[Any, ...]],
    ):
        self.conversation_exists = conversation_exists
        self.event_rows = event_rows
        self._last_fetchone: Any = None
        self._last_fetchall: list[Any] = []

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
        if "from event where conversation_id = %s and seq_no > %s" in normalized_sql:
            self._last_fetchall = self.event_rows
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_list_events_success() -> None:
    ts = datetime(2026, 2, 28, 3, 40, tzinfo=timezone.utc)
    actor_id = uuid4()
    message_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        event_rows=[
            (
                12,
                "turn.committed",
                actor_id,
                message_id,
                {"turn_index": 6},
                ts,
            )
        ],
    )
    service = EventHistoryService(connection)

    rows = service.list_events(conversation_id=uuid4(), limit=10, after_seq_no=11)

    assert len(rows) == 1
    assert rows[0].seq_no == 12
    assert rows[0].event_type == "turn.committed"
    assert rows[0].actor_participant_id == actor_id
    assert rows[0].message_id == message_id
    assert rows[0].payload == {"turn_index": 6}


def test_list_events_conversation_missing() -> None:
    connection = FakeConnection(conversation_exists=False, event_rows=[])
    service = EventHistoryService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.list_events(conversation_id=uuid4(), limit=10, after_seq_no=0)


def test_list_events_invalid_params() -> None:
    connection = FakeConnection(conversation_exists=True, event_rows=[])
    service = EventHistoryService(connection)

    with pytest.raises(ValueError):
        service.list_events(conversation_id=uuid4(), limit=0, after_seq_no=0)

    with pytest.raises(ValueError):
        service.list_events(conversation_id=uuid4(), limit=10, after_seq_no=-1)
