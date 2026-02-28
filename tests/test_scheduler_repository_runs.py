"""Unit tests for scheduler run listing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.scheduler.repository import SchedulerRepository


class FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows
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
        if "from automation_run where" in normalized_sql:
            self.last_sql = normalized_sql
            self.last_params = params
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def test_list_runs_success() -> None:
    template_id = uuid4()
    ts = datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc)
    rows = [
        (11, template_id, ts, "abc123", "triggered", ts, {"source": "cron"}),
        (10, template_id, ts, "abc122", "duplicate", ts, {}),
    ]
    repository = SchedulerRepository(FakeConnection(rows))

    result = repository.list_runs(template_id=template_id, limit=20)

    assert len(result) == 2
    assert result[0].run_id == 11
    assert result[1].status == "duplicate"


def test_list_runs_invalid_limit() -> None:
    repository = SchedulerRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_runs(template_id=uuid4(), limit=0)


def test_list_runs_applies_cursor_filter() -> None:
    template_id = uuid4()
    before_scheduled_for = datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc)
    connection = FakeConnection([])
    repository = SchedulerRepository(connection)

    repository.list_runs(
        template_id=template_id,
        limit=20,
        before_scheduled_for=before_scheduled_for,
        before_run_id=100,
    )

    assert connection.last_sql is not None
    assert "scheduled_for < %s or (scheduled_for = %s and id < %s)" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[1] == before_scheduled_for
    assert connection.last_params[2] == before_scheduled_for
    assert connection.last_params[3] == 100


def test_list_runs_rejects_partial_cursor() -> None:
    repository = SchedulerRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_runs(
            template_id=uuid4(),
            limit=20,
            before_scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
            before_run_id=None,
        )
