"""Unit tests for scheduler template update operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.scheduler.repository import (
    SchedulerRepository,
    TemplateNotFoundError,
    UpdateAutomationTemplateInput,
)


class FakeConnection:
    def __init__(
        self,
        *,
        enabled_row: tuple[Any, ...] | None = None,
        update_row: tuple[Any, ...] | None = None,
    ):
        self.enabled_row = enabled_row
        self.update_row = update_row
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
        if "update automation_template set enabled = %s" in normalized_sql:
            self._last_fetchone = self.enabled_row
            return
        if "update automation_template set name = coalesce(%s, name)" in normalized_sql:
            self._last_fetchone = self.update_row
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_set_template_enabled_success() -> None:
    template_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 20, tzinfo=timezone.utc)
    connection = FakeConnection(enabled_row=(template_id, False, ts))
    repository = SchedulerRepository(connection)

    result = repository.set_template_enabled(template_id=template_id, enabled=False)

    assert result.template_id == template_id
    assert result.enabled is False
    assert result.updated_at == ts
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_set_template_enabled_not_found() -> None:
    connection = FakeConnection(enabled_row=None)
    repository = SchedulerRepository(connection)

    with pytest.raises(TemplateNotFoundError):
        repository.set_template_enabled(template_id=uuid4(), enabled=True)

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_update_template_success() -> None:
    template_id = uuid4()
    ts = datetime(2026, 2, 28, 4, 22, tzinfo=timezone.utc)
    update_row = (
        template_id,
        uuid4(),
        uuid4(),
        "Updated name",
        "Updated objective",
        "FREQ=WEEKLY;BYDAY=FR",
        ["ai_1"],
        True,
        {"v": 2},
        ts,
        ts,
    )
    connection = FakeConnection(update_row=update_row)
    repository = SchedulerRepository(connection)

    result = repository.update_template(
        UpdateAutomationTemplateInput(
            template_id=template_id,
            name="Updated name",
            conversation_objective="Updated objective",
            rrule="FREQ=WEEKLY;BYDAY=FR",
            participants=["ai_1"],
            metadata={"v": 2},
        )
    )

    assert result.template_id == template_id
    assert result.name == "Updated name"
    assert result.conversation_objective == "Updated objective"
    assert result.rrule == "FREQ=WEEKLY;BYDAY=FR"
    assert result.participants == ["ai_1"]
    assert result.metadata == {"v": 2}
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_update_template_requires_any_field() -> None:
    repository = SchedulerRepository(FakeConnection(update_row=None))

    with pytest.raises(ValueError):
        repository.update_template(UpdateAutomationTemplateInput(template_id=uuid4()))


def test_update_template_not_found() -> None:
    connection = FakeConnection(update_row=None)
    repository = SchedulerRepository(connection)

    with pytest.raises(TemplateNotFoundError):
        repository.update_template(
            UpdateAutomationTemplateInput(
                template_id=uuid4(),
                name="x",
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
