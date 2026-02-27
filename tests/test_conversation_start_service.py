"""Unit tests for conversation start service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.conversation_start_service import (
    ConversationStartService,
    ParticipantSeed,
    StartConversationInput,
)


class FakeConnection:
    def __init__(self, *, existing_automation_row: tuple[Any, ...] | None = None):
        self.existing_automation_row = existing_automation_row
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self.inserted_conversation_metadata: dict[str, Any] | None = None
        self.inserted_events: list[tuple[str, dict[str, Any]]] = []
        self.inserted_participants = 0
        self.created_conversation_id = uuid4()

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "from conversation where tenant_id" in normalized_sql:
            self._last_fetchone = self.existing_automation_row
            return
        if "select coalesce(max(seq_no), 0) from event" in normalized_sql:
            self._last_fetchone = (2,)
            return
        if "insert into conversation (" in normalized_sql:
            assert params is not None
            metadata = json.loads(params[5])
            self.inserted_conversation_metadata = metadata
            self._last_fetchone = (
                self.created_conversation_id,
                "active",
                params[4],
                datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc),
            )
            return
        if "insert into participant (" in normalized_sql:
            self.inserted_participants += 1
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            if "conversation.created" in normalized_sql:
                event_type = "conversation.created"
            elif "conversation.started" in normalized_sql:
                event_type = "conversation.started"
            else:
                event_type = "unknown"
            self.inserted_events.append((event_type, json.loads(params[1])))
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def _automation_input() -> StartConversationInput:
    return StartConversationInput(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        title="Automated conversation",
        objective="Default scheduled objective",
        start_trigger="automation",
        metadata={"priority": "high"},
        participants=[
            ParticipantSeed(kind="ai", display_name="AI(1)"),
            ParticipantSeed(kind="ai", display_name="AI(2)"),
        ],
        automation_run_id="run-20260227-2000",
        scheduled_for=datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc),
    )


def test_start_conversation_creates_rows_and_events() -> None:
    connection = FakeConnection(existing_automation_row=None)
    service = ConversationStartService(connection)
    payload = _automation_input()

    result = service.start_conversation(payload)

    assert result.created is True
    assert result.start_trigger == "automation"
    assert result.event_seq_last == 2
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0
    assert connection.inserted_participants == 2
    assert connection.inserted_events[0][0] == "conversation.created"
    assert connection.inserted_events[1][0] == "conversation.started"
    assert connection.inserted_conversation_metadata is not None
    assert connection.inserted_conversation_metadata["automation_run_id"] == (
        "run-20260227-2000"
    )


def test_start_conversation_returns_duplicate_for_same_automation_run() -> None:
    conversation_id = uuid4()
    existing_row = (
        conversation_id,
        "active",
        "automation",
        datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc),
    )
    connection = FakeConnection(existing_automation_row=existing_row)
    service = ConversationStartService(connection)

    result = service.start_conversation(_automation_input())

    assert result.created is False
    assert result.conversation_id == conversation_id
    assert result.event_seq_last == 2
    assert connection.commit_calls == 0
    assert connection.inserted_conversation_metadata is None
    assert connection.inserted_events == []


def test_start_conversation_rejects_invalid_trigger() -> None:
    connection = FakeConnection()
    service = ConversationStartService(connection)

    with pytest.raises(ValueError):
        service.start_conversation(
            StartConversationInput(
                tenant_id=uuid4(),
                workspace_id=uuid4(),
                title="invalid",
                objective="invalid",
                start_trigger="system",
                metadata={},
                participants=[],
            )
        )
