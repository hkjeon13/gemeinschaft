"""Unit tests for scheduler run detail lookup."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.scheduler.repository import AutomationRunNotFoundError, SchedulerRepository


class FakeConnection:
    def __init__(self, row: tuple[Any, ...] | None):
        self.row = row
        self._last_fetchone: Any = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "from automation_run where id = %s" in normalized_sql:
            self._last_fetchone = self.row
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone


def test_get_run_success() -> None:
    template_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 30, tzinfo=timezone.utc)
    row = (11, template_id, ts, "abc123", "failed", ts, {"error": "x"})
    repository = SchedulerRepository(FakeConnection(row))

    result = repository.get_run(11)

    assert result.run_id == 11
    assert result.template_id == template_id
    assert result.status == "failed"
    assert result.metadata == {"error": "x"}


def test_get_run_not_found() -> None:
    repository = SchedulerRepository(FakeConnection(None))

    with pytest.raises(AutomationRunNotFoundError):
        repository.get_run(999)
