"""Unit tests for scheduler run failure persistence."""

from __future__ import annotations

from typing import Any

import pytest

from services.scheduler.repository import (
    AutomationRunNotFoundError,
    SchedulerRepository,
)


class FakeConnection:
    def __init__(self, row: tuple[Any, ...] | None):
        self.row = row
        self._last_fetchone: Any = None
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "update automation_run set status = 'failed'" in normalized_sql:
            self._last_fetchone = self.row
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_mark_run_failed_success() -> None:
    connection = FakeConnection(row=(11,))
    repository = SchedulerRepository(connection)

    repository.mark_run_failed(
        run_id=11,
        error_message="upstream failed",
        metadata={"source": "execute"},
    )

    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_mark_run_failed_not_found() -> None:
    connection = FakeConnection(row=None)
    repository = SchedulerRepository(connection)

    with pytest.raises(AutomationRunNotFoundError):
        repository.mark_run_failed(
            run_id=999,
            error_message="missing",
            metadata={},
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
