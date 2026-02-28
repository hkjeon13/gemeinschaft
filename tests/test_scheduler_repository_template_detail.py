"""Unit tests for scheduler template detail lookup."""

from __future__ import annotations

from datetime import datetime, timezone
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


def test_get_template_detail_success() -> None:
    template_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 15, tzinfo=timezone.utc)
    row = (
        template_id,
        uuid4(),
        uuid4(),
        "Daily digest",
        "Summarize updates",
        "FREQ=WEEKLY;BYDAY=MO",
        ["ai_1", "human_moderator"],
        True,
        {"source": "test"},
        ts,
        ts,
    )
    repository = SchedulerRepository(FakeConnection(row))

    result = repository.get_template_detail(template_id)

    assert result.template_id == template_id
    assert result.name == "Daily digest"
    assert result.rrule.startswith("FREQ=WEEKLY")
    assert result.participants == ["ai_1", "human_moderator"]
    assert result.created_at == ts


def test_get_template_detail_not_found() -> None:
    repository = SchedulerRepository(FakeConnection(None))

    with pytest.raises(TemplateNotFoundError):
        repository.get_template_detail(uuid4())
