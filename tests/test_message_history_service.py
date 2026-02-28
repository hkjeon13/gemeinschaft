"""Unit tests for conversation message history service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.message_history_service import MessageHistoryService


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        message_rows: list[tuple[Any, ...]],
    ):
        self.conversation_exists = conversation_exists
        self.message_rows = message_rows
        self._last_fetchone: Any = None
        self._last_fetchall: list[Any] = []
        self.last_message_sql: str | None = None

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
            self.last_message_sql = normalized_sql
            self._last_fetchall = self.message_rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def test_list_messages_success() -> None:
    ts = datetime(2026, 2, 28, 5, 0, tzinfo=timezone.utc)
    message_id = uuid4()
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        message_rows=[
            (
                4,
                message_id,
                participant_id,
                "AI(1)",
                "ai",
                "committed",
                "statement",
                "grounded answer",
                {"validation": {"is_valid": True}},
                ts,
            )
        ],
    )
    service = MessageHistoryService(connection)

    rows = service.list_messages(conversation_id=uuid4(), limit=20, after_turn_index=0)

    assert len(rows) == 1
    assert rows[0].turn_index == 4
    assert rows[0].message_id == message_id
    assert rows[0].participant_name == "AI(1)"
    assert rows[0].status == "committed"


def test_list_messages_with_status_filter() -> None:
    connection = FakeConnection(conversation_exists=True, message_rows=[])
    service = MessageHistoryService(connection)

    service.list_messages(conversation_id=uuid4(), limit=20, status="rejected")

    assert connection.last_message_sql is not None
    assert "and m.status = %s" in connection.last_message_sql


def test_list_messages_conversation_not_found() -> None:
    connection = FakeConnection(conversation_exists=False, message_rows=[])
    service = MessageHistoryService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.list_messages(conversation_id=uuid4(), limit=20)


def test_list_messages_invalid_status_filter() -> None:
    connection = FakeConnection(conversation_exists=True, message_rows=[])
    service = MessageHistoryService(connection)

    with pytest.raises(ValueError):
        service.list_messages(conversation_id=uuid4(), limit=20, status="unknown")
