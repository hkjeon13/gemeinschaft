"""Unit tests for scheduler template lookup."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from services.scheduler.repository import SchedulerRepository, TemplateNotFoundError


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
        if "from automation_template where id = %s" in normalized_sql:
            self._last_fetchone = self.row
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone


def test_get_template_success() -> None:
    template_id = uuid4()
    row = (
        template_id,
        uuid4(),
        uuid4(),
        "Hourly default",
        "Generate periodic insights",
        ["ai_1", "ai_2"],
        True,
        {"source": "test"},
    )
    repository = SchedulerRepository(FakeConnection(row))

    record = repository.get_template(template_id)

    assert record.template_id == template_id
    assert record.name == "Hourly default"
    assert record.participants == ["ai_1", "ai_2"]
    assert record.enabled is True


def test_get_template_not_found() -> None:
    repository = SchedulerRepository(FakeConnection(None))

    with pytest.raises(TemplateNotFoundError):
        repository.get_template(uuid4())
