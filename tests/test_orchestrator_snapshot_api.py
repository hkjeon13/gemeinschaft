"""API tests for conversation snapshot rebuild endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module


class FakeSnapshotConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        event_rows: list[tuple[int, str, dict[str, Any], datetime]],
    ):
        self.conversation_exists = conversation_exists
        self.event_rows = event_rows
        self._last_fetchone: Any = None
        self._last_fetchall: Any = []

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
            return
        raise AssertionError(f"Unexpected SQL in test fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_rebuild_snapshot_endpoint_success(monkeypatch: Any) -> None:
    connection = FakeSnapshotConnection(
        conversation_exists=True,
        event_rows=[
            (1, "conversation.created", {}, datetime(2026, 2, 27, 9, tzinfo=timezone.utc)),
            (2, "conversation.started", {}, datetime(2026, 2, 27, 10, tzinfo=timezone.utc)),
            (
                3,
                "turn.committed",
                {"turn_index": 1},
                datetime(2026, 2, 27, 11, tzinfo=timezone.utc),
            ),
        ],
    )
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(f"/internal/snapshots/rebuild/{conversation_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["status"] == "active"
    assert payload["last_seq_no"] == 3
    assert payload["turn_count"] == 1


def test_rebuild_snapshot_endpoint_not_found(monkeypatch: Any) -> None:
    connection = FakeSnapshotConnection(conversation_exists=False, event_rows=[])
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    client = TestClient(orchestrator_app_module.app)

    response = client.post(f"/internal/snapshots/rebuild/{uuid4()}")

    assert response.status_code == 404
