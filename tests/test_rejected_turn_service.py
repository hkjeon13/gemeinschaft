"""Unit tests for rejected turn review service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.rejected_turn_service import RejectedTurnService


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        rejected_rows: list[tuple[Any, ...]],
    ):
        self.conversation_exists = conversation_exists
        self.rejected_rows = rejected_rows
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
            self._last_fetchall = self.rejected_rows
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_list_rejected_turns_success() -> None:
    ts = datetime(2026, 2, 28, 2, 20, tzinfo=timezone.utc)
    message_id = uuid4()
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        rejected_rows=[
            (
                8,
                message_id,
                participant_id,
                "AI(1)",
                "ai",
                "ungrounded text",
                "missing_citation",
                ["ai turn must include at least one citation"],
                ts,
                {"validation": {"is_valid": False, "failure_type": "missing_citation"}},
            )
        ],
    )
    service = RejectedTurnService(connection)

    rows = service.list_rejected_turns(conversation_id=uuid4(), limit=20)

    assert len(rows) == 1
    assert rows[0].turn_index == 8
    assert rows[0].message_id == message_id
    assert rows[0].participant_id == participant_id
    assert rows[0].failure_type == "missing_citation"
    assert rows[0].reasons == ["ai turn must include at least one citation"]


def test_list_rejected_turns_conversation_missing() -> None:
    connection = FakeConnection(conversation_exists=False, rejected_rows=[])
    service = RejectedTurnService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.list_rejected_turns(conversation_id=uuid4(), limit=20)


def test_list_rejected_turns_invalid_limit() -> None:
    connection = FakeConnection(conversation_exists=True, rejected_rows=[])
    service = RejectedTurnService(connection)

    with pytest.raises(ValueError):
        service.list_rejected_turns(conversation_id=uuid4(), limit=0)


def test_list_rejected_turns_applies_before_turn_index_filter() -> None:
    connection = FakeConnection(conversation_exists=True, rejected_rows=[])
    service = RejectedTurnService(connection)

    service.list_rejected_turns(conversation_id=uuid4(), limit=20, before_turn_index=8)

    assert connection.last_sql is not None
    assert "m.turn_index < %s" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[1] == 8


def test_list_rejected_turns_invalid_before_turn_index() -> None:
    connection = FakeConnection(conversation_exists=True, rejected_rows=[])
    service = RejectedTurnService(connection)

    with pytest.raises(ValueError):
        service.list_rejected_turns(conversation_id=uuid4(), limit=20, before_turn_index=0)
