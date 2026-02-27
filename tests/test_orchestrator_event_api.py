"""API tests for conversation event append endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module


class FakeConnection:
    def __init__(self, *, conversation_exists: bool, current_seq_no: int):
        self.conversation_exists = conversation_exists
        self.current_seq_no = current_seq_no
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
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_append_event_endpoint_success(monkeypatch: Any) -> None:
    connection = FakeConnection(conversation_exists=True, current_seq_no=0)
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        "/internal/events/append",
        json={
            "conversation_id": conversation_id,
            "event_type": "conversation.started",
            "expected_seq_no": 0,
            "payload": {"trigger": "automation"},
        },
    )

    assert response.status_code == 201
    assert response.json()["event_id"] == 1
    assert response.json()["seq_no"] == 1


def test_append_event_endpoint_sequence_conflict(monkeypatch: Any) -> None:
    connection = FakeConnection(conversation_exists=True, current_seq_no=3)
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        "/internal/events/append",
        json={
            "conversation_id": str(uuid4()),
            "event_type": "turn.committed",
            "expected_seq_no": 2,
            "payload": {},
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["expected_seq_no"] == 2
    assert detail["actual_seq_no"] == 3


def test_append_event_endpoint_not_found(monkeypatch: Any) -> None:
    connection = FakeConnection(conversation_exists=False, current_seq_no=0)
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        "/internal/events/append",
        json={
            "conversation_id": str(uuid4()),
            "event_type": "conversation.started",
            "expected_seq_no": 0,
            "payload": {},
        },
    )

    assert response.status_code == 404
