"""Unit tests for source repository list query."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.data_ingestion.source_repository import SourceRepository


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
        if "from source_document" in normalized_sql:
            self.last_sql = normalized_sql
            self.last_params = params
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def test_list_sources_success() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    ts = datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
    rows = [
        (
            uuid4(),
            tenant_id,
            workspace_id,
            "upload",
            "a.txt",
            "text/plain",
            123,
            "sha-a",
            "local_fs",
            "k/a",
            {"origin": "test"},
            ts,
        )
    ]
    repository = SourceRepository(FakeConnection(rows))

    result = repository.list_sources(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=20,
    )

    assert len(result) == 1
    assert result[0].tenant_id == tenant_id
    assert result[0].workspace_id == workspace_id
    assert result[0].source_type == "upload"
    assert result[0].byte_size == 123


def test_list_sources_invalid_limit() -> None:
    repository = SourceRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_sources(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            limit=0,
        )


def test_list_sources_invalid_source_type() -> None:
    repository = SourceRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_sources(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            source_type="unknown",
            limit=20,
        )


def test_list_sources_applies_source_type_filter() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    connection = FakeConnection([])
    repository = SourceRepository(connection)

    repository.list_sources(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_type="upload",
        limit=10,
    )

    assert connection.last_sql is not None
    assert "source_type = %s" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[2] == "upload"


def test_list_sources_applies_cursor_filter() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    before_created_at = datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc)
    before_source_id = uuid4()
    connection = FakeConnection([])
    repository = SourceRepository(connection)

    repository.list_sources(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=10,
        before_created_at=before_created_at,
        before_source_id=before_source_id,
    )

    assert connection.last_sql is not None
    assert "created_at < %s or (created_at = %s and id < %s)" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[2] == before_created_at
    assert connection.last_params[3] == before_created_at
    assert connection.last_params[4] == str(before_source_id)


def test_list_sources_rejects_partial_cursor() -> None:
    repository = SourceRepository(FakeConnection([]))

    with pytest.raises(ValueError):
        repository.list_sources(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            limit=10,
            before_created_at=datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc),
            before_source_id=None,
        )
