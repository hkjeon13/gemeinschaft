"""Unit tests for scheduler template listing."""

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
        if "from automation_template" in normalized_sql:
            self.last_sql = normalized_sql
            self.last_params = params
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def test_list_templates_enabled_only_default() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 10, tzinfo=timezone.utc)
    rows = [(uuid4(), tenant_id, workspace_id, "Hourly", "FREQ=HOURLY", True, ts, ts)]
    repository = SchedulerRepository(FakeConnection(rows))

    result = repository.list_templates(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=20,
    )

    assert len(result) == 1
    assert result[0].name == "Hourly"
    assert result[0].enabled is True
    assert repository._connection.last_sql is not None
    assert "enabled = true" in repository._connection.last_sql


def test_list_templates_include_disabled() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 12, tzinfo=timezone.utc)
    rows = [
        (uuid4(), tenant_id, workspace_id, "A", "FREQ=HOURLY", True, ts, ts),
        (uuid4(), tenant_id, workspace_id, "B", "FREQ=WEEKLY", False, ts, ts),
    ]
    connection = FakeConnection(rows)
    repository = SchedulerRepository(connection)

    result = repository.list_templates(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        include_disabled=True,
        limit=20,
    )

    assert len(result) == 2
    assert result[1].enabled is False
    assert connection.last_sql is not None
    assert "enabled = true" not in connection.last_sql


def test_list_templates_invalid_limit() -> None:
    repository = SchedulerRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_templates(tenant_id=uuid4(), workspace_id=uuid4(), limit=0)


def test_list_templates_applies_cursor_filter() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 12, tzinfo=timezone.utc)
    cursor_time = datetime(2026, 2, 28, 4, 11, tzinfo=timezone.utc)
    cursor_id = uuid4()
    connection = FakeConnection(
        [(uuid4(), tenant_id, workspace_id, "A", "FREQ=HOURLY", True, ts, ts)]
    )
    repository = SchedulerRepository(connection)

    repository.list_templates(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=10,
        before_updated_at=cursor_time,
        before_template_id=cursor_id,
    )

    assert connection.last_sql is not None
    assert "updated_at < %s or (updated_at = %s and id < %s)" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[2] == cursor_time
    assert connection.last_params[3] == cursor_time
    assert connection.last_params[4] == str(cursor_id)


def test_list_templates_rejects_partial_cursor() -> None:
    repository = SchedulerRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_templates(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            limit=10,
            before_updated_at=datetime(2026, 2, 28, 4, 11, tzinfo=timezone.utc),
            before_template_id=None,
        )
