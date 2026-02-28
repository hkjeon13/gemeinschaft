"""API tests for participant moderation endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_moderation_service import (
    ApplyParticipantModerationResult,
    InvalidModerationActionError,
    ParticipantModerationNotFoundError,
    ParticipantModerationStateError,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessParticipantModerationService:
    def apply(self, payload: Any) -> ApplyParticipantModerationResult:
        return ApplyParticipantModerationResult(
            conversation_id=payload.conversation_id,
            participant_id=payload.participant_id,
            muted=True,
            event_type="participant.muted",
            event_seq_last=51,
            occurred_at=datetime(2026, 2, 28, 2, 10, tzinfo=timezone.utc),
        )


class MissingConversationParticipantModerationService:
    def apply(self, payload: Any) -> ApplyParticipantModerationResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class MissingParticipantModerationService:
    def apply(self, payload: Any) -> ApplyParticipantModerationResult:
        raise ParticipantModerationNotFoundError(f"Participant {payload.participant_id} not found")


class InvalidActionParticipantModerationService:
    def apply(self, payload: Any) -> ApplyParticipantModerationResult:
        raise InvalidModerationActionError("Unsupported action")


class StateErrorParticipantModerationService:
    def apply(self, payload: Any) -> ApplyParticipantModerationResult:
        raise ParticipantModerationStateError("Participant is already muted")


def test_apply_participant_moderation_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_moderation_service",
        lambda connection: SuccessParticipantModerationService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/moderation",
        json={"action": "mute", "reason": "off-topic"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["muted"] is True
    assert payload["event_type"] == "participant.muted"
    assert payload["event_seq_last"] == 51


def test_apply_participant_moderation_endpoint_conversation_not_found(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_moderation_service",
        lambda connection: MissingConversationParticipantModerationService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/moderation",
        json={"action": "mute"},
    )

    assert response.status_code == 404


def test_apply_participant_moderation_endpoint_participant_not_found(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_moderation_service",
        lambda connection: MissingParticipantModerationService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/moderation",
        json={"action": "mute"},
    )

    assert response.status_code == 404


def test_apply_participant_moderation_endpoint_invalid_action(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_moderation_service",
        lambda connection: InvalidActionParticipantModerationService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/moderation",
        json={"action": "pause"},
    )

    assert response.status_code == 400


def test_apply_participant_moderation_endpoint_state_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_moderation_service",
        lambda connection: StateErrorParticipantModerationService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/participants/{uuid4()}/moderation",
        json={"action": "mute"},
    )

    assert response.status_code == 409
