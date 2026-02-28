"""API tests for pending approval turns endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.pending_turn_service import PendingTurnRecord


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessPendingTurnService:
    def list_pending_turns(self, conversation_id: Any, limit: int) -> list[PendingTurnRecord]:
        return [
            PendingTurnRecord(
                turn_index=5,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                content_text="pending response",
                created_at=datetime(2026, 2, 28, 0, 20, tzinfo=timezone.utc),
                metadata={"validation": {"is_valid": True}},
            )
        ]


class MissingConversationPendingTurnService:
    def list_pending_turns(self, conversation_id: Any, limit: int) -> list[PendingTurnRecord]:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class InvalidLimitPendingTurnService:
    def list_pending_turns(self, conversation_id: Any, limit: int) -> list[PendingTurnRecord]:
        raise ValueError("limit must be >= 1")


class CursorPendingTurnService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def list_pending_turns(
        self,
        *,
        conversation_id: Any,
        limit: int,
        after_turn_index: int = 0,
    ) -> list[PendingTurnRecord]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "after_turn_index": after_turn_index,
            }
        )
        ts = datetime(2026, 2, 28, 0, 20, tzinfo=timezone.utc)
        return [
            PendingTurnRecord(
                turn_index=after_turn_index + i,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                content_text=f"pending-{i}",
                created_at=ts,
                metadata={},
            )
            for i in range(1, limit + 1)
        ]


def test_list_pending_approval_turns_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: SuccessPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval",
        params={"limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["turn_index"] == 5
    assert payload[0]["participant_name"] == "AI(2)"


def test_list_pending_approval_turns_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: MissingConversationPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/turns/pending-approval")

    assert response.status_code == 404


def test_list_pending_approval_turns_invalid_limit(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: InvalidLimitPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval",
        params={"limit": 0},
    )

    assert response.status_code in {400, 422}


def test_list_pending_approval_turns_page_success(monkeypatch: Any) -> None:
    service = CursorPendingTurnService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval/page",
        params={"limit": 2, "cursor": "turn:5"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["turn_index"] == 6
    assert payload["items"][1]["turn_index"] == 7
    assert payload["next_cursor"] == "turn:7"
    assert payload["has_more"] is True
    assert service.calls[0]["limit"] == 3
    assert service.calls[0]["after_turn_index"] == 5


def test_list_pending_approval_turns_page_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: CursorPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval/page",
        params={"cursor": "bad"},
    )

    assert response.status_code == 400
