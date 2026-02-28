"""API tests for conversation failure summary endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.failure_summary_service import (
    ConversationFailureSummary,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessFailureSummaryService:
    def get_summary(self, conversation_id: Any) -> ConversationFailureSummary:
        return ConversationFailureSummary(
            conversation_id=conversation_id,
            rejected_turns=9,
            missing_citation_count=4,
            invalid_citation_count=1,
            loop_risk_repetition_count=2,
            topic_derailment_count=2,
            loop_guard_trigger_count=1,
            arbitration_requested_count=1,
        )


class MissingConversationFailureSummaryService:
    def get_summary(self, conversation_id: Any) -> ConversationFailureSummary:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


def test_get_conversation_failure_summary_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_failure_summary_service",
        lambda connection: SuccessFailureSummaryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/ops/failures")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rejected_turns"] == 9
    assert payload["missing_citation_count"] == 4
    assert payload["topic_derailment_count"] == 2
    assert payload["loop_guard_trigger_count"] == 1


def test_get_conversation_failure_summary_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_failure_summary_service",
        lambda connection: MissingConversationFailureSummaryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/ops/failures")

    assert response.status_code == 404
