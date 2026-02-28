"""API tests for participant role switching endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_role_service import (
    ParticipantNotFoundError,
    SwitchParticipantRoleResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessParticipantRoleService:
    def switch_role(self, payload: Any) -> SwitchParticipantRoleResult:
        return SwitchParticipantRoleResult(
            conversation_id=payload.conversation_id,
            participant_id=payload.participant_id,
            previous_role_label="ai_observer",
            new_role_label="ai_critic",
            event_seq_last=41,
            occurred_at=datetime(2026, 2, 28, 1, 10, tzinfo=timezone.utc),
        )


class MissingConversationParticipantRoleService:
    def switch_role(self, payload: Any) -> SwitchParticipantRoleResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class MissingParticipantRoleService:
    def switch_role(self, payload: Any) -> SwitchParticipantRoleResult:
        raise ParticipantNotFoundError(f"Participant {payload.participant_id} not found")


class InvalidRoleLabelService:
    def switch_role(self, payload: Any) -> SwitchParticipantRoleResult:
        raise ValueError("new_role_label must not be empty")


def test_switch_participant_role_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_role_service",
        lambda connection: SuccessParticipantRoleService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/role/switch",
        json={"new_role_label": "ai_critic", "reason": "rebalance"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["previous_role_label"] == "ai_observer"
    assert payload["new_role_label"] == "ai_critic"
    assert payload["event_seq_last"] == 41


def test_switch_participant_role_endpoint_conversation_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_role_service",
        lambda connection: MissingConversationParticipantRoleService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/role/switch",
        json={"new_role_label": "moderator"},
    )

    assert response.status_code == 404


def test_switch_participant_role_endpoint_participant_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_role_service",
        lambda connection: MissingParticipantRoleService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/role/switch",
        json={"new_role_label": "moderator"},
    )

    assert response.status_code == 404


def test_switch_participant_role_endpoint_invalid_role(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_role_service",
        lambda connection: InvalidRoleLabelService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/role/switch",
        json={"new_role_label": " "},
    )

    assert response.status_code in {400, 422}
