"""API tests for human intervention endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.intervention_service import (
    ApplyInterventionResult,
    InvalidInterventionStateError,
    InvalidInterventionTypeError,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessInterventionService:
    def apply_intervention(self, payload: Any) -> ApplyInterventionResult:
        return ApplyInterventionResult(
            conversation_id=payload.conversation_id,
            status="paused",
            event_seq_last=14,
            applied_events=["human.intervention", "conversation.paused"],
            occurred_at=datetime(2026, 2, 27, 21, 10, tzinfo=timezone.utc),
        )


class NotFoundInterventionService:
    def apply_intervention(self, payload: Any) -> ApplyInterventionResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class InvalidTypeInterventionService:
    def apply_intervention(self, payload: Any) -> ApplyInterventionResult:
        raise InvalidInterventionTypeError("Unsupported intervention_type: wrong")


class InvalidStateInterventionService:
    def apply_intervention(self, payload: Any) -> ApplyInterventionResult:
        raise InvalidInterventionStateError(
            "Cannot apply resume when conversation status is active"
        )


def test_apply_intervention_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_intervention_service",
        lambda connection: SuccessInterventionService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/interventions/apply",
        json={
            "intervention_type": "interrupt",
            "instruction": "pause for review",
            "metadata": {"reason": "qa"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "paused"
    assert payload["event_seq_last"] == 14
    assert payload["applied_events"] == ["human.intervention", "conversation.paused"]


def test_apply_intervention_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_intervention_service",
        lambda connection: NotFoundInterventionService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/interventions/apply",
        json={"intervention_type": "interrupt"},
    )

    assert response.status_code == 404


def test_apply_intervention_endpoint_invalid_type(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_intervention_service",
        lambda connection: InvalidTypeInterventionService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/interventions/apply",
        json={"intervention_type": "wrong"},
    )

    assert response.status_code == 400


def test_apply_intervention_endpoint_invalid_state(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_intervention_service",
        lambda connection: InvalidStateInterventionService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/interventions/apply",
        json={"intervention_type": "resume"},
    )

    assert response.status_code == 409
