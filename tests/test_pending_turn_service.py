"""Unit tests for pending turn read service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.pending_turn_service import PendingTurnService


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        pending_rows: list[tuple[Any, ...]],
    ):
        self.conversation_exists = conversation_exists
        self.pending_rows = pending_rows
        self._last_fetchone: Any = None
        self._last_fetchall: list[Any] = []
        self.last_sql: str | None = None
        self.last_params: tuple[Any, ...] | None = None

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
        if "from message m join participant p" in normalized_sql:
            self.last_sql = normalized_sql
            self.last_params = params
            self._last_fetchall = self.pending_rows
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_list_pending_turns_success() -> None:
    ts = datetime(2026, 2, 28, 0, 10, tzinfo=timezone.utc)
    message_id = uuid4()
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        pending_rows=[
            (
                3,
                message_id,
                participant_id,
                "AI(1)",
                "ai",
                "proposed answer",
                ts,
                {"generation": {"generator": "agent_runtime"}},
            )
        ],
    )
    service = PendingTurnService(connection)

    rows = service.list_pending_turns(conversation_id=uuid4(), limit=20)

    assert len(rows) == 1
    assert rows[0].turn_index == 3
    assert rows[0].message_id == message_id
    assert rows[0].participant_id == participant_id
    assert rows[0].participant_name == "AI(1)"


def test_list_pending_turns_conversation_missing() -> None:
    connection = FakeConnection(conversation_exists=False, pending_rows=[])
    service = PendingTurnService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.list_pending_turns(conversation_id=uuid4(), limit=20)


def test_list_pending_turns_invalid_limit() -> None:
    connection = FakeConnection(conversation_exists=True, pending_rows=[])
    service = PendingTurnService(connection)

    with pytest.raises(ValueError):
        service.list_pending_turns(conversation_id=uuid4(), limit=0)


def test_list_pending_turns_applies_after_turn_index_filter() -> None:
    connection = FakeConnection(conversation_exists=True, pending_rows=[])
    service = PendingTurnService(connection)

    service.list_pending_turns(conversation_id=uuid4(), limit=20, after_turn_index=3)

    assert connection.last_sql is not None
    assert "m.turn_index > %s" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[1] == 3


def test_list_pending_turns_invalid_after_turn_index() -> None:
    connection = FakeConnection(conversation_exists=True, pending_rows=[])
    service = PendingTurnService(connection)

    with pytest.raises(ValueError):
        service.list_pending_turns(conversation_id=uuid4(), limit=20, after_turn_index=-1)
