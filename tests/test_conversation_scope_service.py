"""Unit tests for conversation scope resolver."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.conversation_scope_service import (
    ConversationScopeService,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError


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
        if "select tenant_id, workspace_id from conversation where id = %s" in normalized_sql:
            self._last_fetchone = self.row
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone


def test_get_scope_success() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    service = ConversationScopeService(FakeConnection((tenant_id, workspace_id)))

    result = service.get_scope(uuid4())

    assert result.tenant_id == tenant_id
    assert result.workspace_id == workspace_id


def test_get_scope_not_found() -> None:
    service = ConversationScopeService(FakeConnection(None))

    with pytest.raises(ConversationNotFoundError):
        service.get_scope(uuid4())
