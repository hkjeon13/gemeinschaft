"""API tests for turn approval endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.turn_approval_service import (
    ApplyTurnApprovalResult,
    InvalidApprovalDecisionError,
    TurnApprovalStateError,
    TurnNotFoundError,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessTurnApprovalService:
    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        return ApplyTurnApprovalResult(
            conversation_id=payload.conversation_id,
            turn_index=payload.turn_index,
            message_status="committed",
            event_seq_last=22,
            applied_events=["turn.approved", "turn.committed"],
            occurred_at=datetime(2026, 2, 27, 23, 10, tzinfo=timezone.utc),
        )


class ConversationMissingTurnApprovalService:
    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class TurnMissingApprovalService:
    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        raise TurnNotFoundError("Turn not found")


class InvalidDecisionApprovalService:
    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        raise InvalidApprovalDecisionError("Unsupported decision")


class StateErrorApprovalService:
    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        raise TurnApprovalStateError("Turn is not proposed")


def test_turn_approval_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_turn_approval_service",
        lambda connection: SuccessTurnApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/3/approval",
        json={"decision": "approve", "reason": "ok"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message_status"] == "committed"
    assert payload["event_seq_last"] == 22


def test_turn_approval_endpoint_conversation_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_turn_approval_service",
        lambda connection: ConversationMissingTurnApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/1/approval",
        json={"decision": "approve"},
    )

    assert response.status_code == 404


def test_turn_approval_endpoint_turn_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_turn_approval_service",
        lambda connection: TurnMissingApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/1/approval",
        json={"decision": "approve"},
    )

    assert response.status_code == 404


def test_turn_approval_endpoint_invalid_decision(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_turn_approval_service",
        lambda connection: InvalidDecisionApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/1/approval",
        json={"decision": "skip"},
    )

    assert response.status_code == 400


def test_turn_approval_endpoint_state_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_turn_approval_service",
        lambda connection: StateErrorApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/1/approval",
        json={"decision": "approve"},
    )

    assert response.status_code == 409
