"""Unit tests for conversation event export service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.conversation_orchestrator.event_export_service import EventExportService


class FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows
        self._last_fetchone: Any = ("conversation",)
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
            self._last_fetchone = ("conversation",)
            return
        if "from event where conversation_id = %s and seq_no > %s" in normalized_sql:
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_event_export_jsonl() -> None:
    ts = datetime(2026, 2, 28, 4, 0, tzinfo=timezone.utc)
    actor_id = uuid4()
    message_id = uuid4()
    connection = FakeConnection(
        rows=[(5, "turn.committed", actor_id, message_id, {"turn_index": 3}, ts)]
    )
    service = EventExportService(connection)

    payload = service.export_jsonl(conversation_id=uuid4(), limit=100, after_seq_no=0)

    lines = payload.decode("utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["seq_no"] == 5
    assert row["event_type"] == "turn.committed"
    assert row["actor_participant_id"] == str(actor_id)
    assert row["message_id"] == str(message_id)
    assert row["payload"] == {"turn_index": 3}
