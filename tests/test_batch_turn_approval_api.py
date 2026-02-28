"""API tests for batch turn approval endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.batch_turn_approval_service import (
    BatchTurnApprovalItemResult,
    BatchTurnApprovalResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessBatchTurnApprovalService:
    def apply_batch(self, payload: Any) -> BatchTurnApprovalResult:
        return BatchTurnApprovalResult(
            conversation_id=payload.conversation_id,
            processed=2,
            approved=1,
            rejected=1,
            failed=0,
            results=[
                BatchTurnApprovalItemResult(
                    turn_index=1,
                    success=True,
                    message_status="committed",
                    event_seq_last=31,
                    applied_events=["turn.approved", "turn.committed"],
                    error_code=None,
                    error_message=None,
                ),
                BatchTurnApprovalItemResult(
                    turn_index=2,
                    success=True,
                    message_status="rejected",
                    event_seq_last=32,
                    applied_events=["turn.rejected"],
                    error_code=None,
                    error_message=None,
                ),
            ],
        )


def test_apply_batch_turn_approval_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_batch_turn_approval_service",
        lambda connection: SuccessBatchTurnApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/approval/batch",
        json={
            "decisions": [
                {"turn_index": 1, "decision": "approve"},
                {"turn_index": 2, "decision": "reject", "reason": "off-topic"},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 2
    assert payload["approved"] == 1
    assert payload["rejected"] == 1
    assert payload["failed"] == 0
    assert payload["results"][0]["message_status"] == "committed"


def test_apply_batch_turn_approval_endpoint_validation_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_batch_turn_approval_service",
        lambda connection: SuccessBatchTurnApprovalService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/turns/approval/batch",
        json={"decisions": []},
    )

    assert response.status_code == 422
