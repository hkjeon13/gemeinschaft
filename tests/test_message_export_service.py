"""Unit tests for conversation message export service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.conversation_orchestrator.message_export_service import MessageExportService


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
        if "from message m join participant p" in normalized_sql:
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def test_message_export_jsonl() -> None:
    ts = datetime(2026, 2, 28, 5, 10, tzinfo=timezone.utc)
    message_id = uuid4()
    participant_id = uuid4()
    connection = FakeConnection(
        rows=[
            (
                2,
                message_id,
                participant_id,
                "Reviewer",
                "human",
                "committed",
                "statement",
                "looks good",
                {"meta": "x"},
                ts,
            )
        ]
    )
    service = MessageExportService(connection)

    payload = service.export_jsonl(conversation_id=uuid4(), limit=100, after_turn_index=0)

    lines = payload.decode("utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["turn_index"] == 2
    assert row["message_id"] == str(message_id)
    assert row["participant_id"] == str(participant_id)
    assert row["participant_name"] == "Reviewer"
    assert row["status"] == "committed"
