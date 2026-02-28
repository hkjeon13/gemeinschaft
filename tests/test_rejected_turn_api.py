"""API tests for rejected turns endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.rejected_turn_service import RejectedTurnRecord


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessRejectedTurnService:
    def list_rejected_turns(self, conversation_id: Any, limit: int) -> list[RejectedTurnRecord]:
        return [
            RejectedTurnRecord(
                turn_index=11,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                content_text="off-topic answer",
                failure_type="topic_derailment",
                reasons=["ai turn does not sufficiently align with objective/topic keywords"],
                created_at=datetime(2026, 2, 28, 2, 30, tzinfo=timezone.utc),
                metadata={"validation": {"is_valid": False}},
            )
        ]


class MissingConversationRejectedTurnService:
    def list_rejected_turns(self, conversation_id: Any, limit: int) -> list[RejectedTurnRecord]:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class InvalidLimitRejectedTurnService:
    def list_rejected_turns(self, conversation_id: Any, limit: int) -> list[RejectedTurnRecord]:
        raise ValueError("limit must be >= 1")


class CursorRejectedTurnService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def list_rejected_turns(
        self,
        *,
        conversation_id: Any,
        limit: int,
        before_turn_index: int | None = None,
    ) -> list[RejectedTurnRecord]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "before_turn_index": before_turn_index,
            }
        )
        ts = datetime(2026, 2, 28, 2, 30, tzinfo=timezone.utc)
        base = 11 if before_turn_index is None else before_turn_index - 1
        return [
            RejectedTurnRecord(
                turn_index=base - i,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                content_text=f"rejected-{i}",
                failure_type="topic_derailment",
                reasons=["ai turn does not sufficiently align with objective/topic keywords"],
                created_at=ts,
                metadata={"validation": {"is_valid": False}},
            )
            for i in range(limit)
        ]


def test_list_rejected_turns_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: SuccessRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected",
        params={"limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["turn_index"] == 11
    assert payload[0]["failure_type"] == "topic_derailment"


def test_list_rejected_turns_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: MissingConversationRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/turns/rejected")

    assert response.status_code == 404


def test_list_rejected_turns_endpoint_invalid_limit(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: InvalidLimitRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected",
        params={"limit": 0},
    )

    assert response.status_code in {400, 422}


def test_list_rejected_turns_page_endpoint_success(monkeypatch: Any) -> None:
    service = CursorRejectedTurnService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected/page",
        params={"limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["turn_index"] == 11
    assert payload["items"][1]["turn_index"] == 10
    assert payload["next_cursor"] == "turn:10"
    assert payload["has_more"] is True
    assert service.calls[0]["limit"] == 3
    assert service.calls[0]["before_turn_index"] is None

    next_response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected/page",
        params={"limit": 2, "cursor": payload["next_cursor"]},
    )
    assert next_response.status_code == 200
    next_payload = next_response.json()
    assert len(next_payload["items"]) == 2
    assert next_payload["items"][0]["turn_index"] == 9
    assert next_payload["items"][1]["turn_index"] == 8
    assert service.calls[1]["limit"] == 3
    assert service.calls[1]["before_turn_index"] == 10


def test_list_rejected_turns_page_endpoint_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: CursorRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected/page",
        params={"cursor": "bad"},
    )

    assert response.status_code == 400
