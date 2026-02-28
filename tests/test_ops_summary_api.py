"""API tests for conversation ops summary endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.ops_summary_service import (
    ConversationOpsSummary,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessOpsSummaryService:
    def get_summary(self, conversation_id: Any) -> ConversationOpsSummary:
        ts = datetime(2026, 2, 28, 0, 50, tzinfo=timezone.utc)
        return ConversationOpsSummary(
            conversation_id=conversation_id,
            status="active",
            title="Ops title",
            objective="ops objective",
            updated_at=ts,
            participant_count=3,
            total_messages=9,
            committed_messages=5,
            proposed_messages=2,
            rejected_messages=2,
            validated_messages=0,
            last_event_seq_no=21,
            last_event_type="turn.pending_approval",
            last_event_at=ts,
        )


class MissingOpsSummaryService:
    def get_summary(self, conversation_id: Any) -> ConversationOpsSummary:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


def test_get_ops_summary_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_ops_summary_service",
        lambda connection: SuccessOpsSummaryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/ops/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "active"
    assert payload["participant_count"] == 3
    assert payload["proposed_messages"] == 2
    assert payload["last_event_seq_no"] == 21


def test_get_ops_summary_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_ops_summary_service",
        lambda connection: MissingOpsSummaryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/ops/summary")

    assert response.status_code == 404
