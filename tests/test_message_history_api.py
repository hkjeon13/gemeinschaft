"""API tests for conversation message history endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.message_history_service import (
    ConversationMessageRecord,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessMessageHistoryService:
    def list_messages(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        del limit, after_turn_index, status
        return [
            ConversationMessageRecord(
                turn_index=3,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                status="proposed",
                message_type="statement",
                content_text="pending answer",
                metadata={"validation": {"is_valid": True}},
                created_at=datetime(2026, 2, 28, 5, 20, tzinfo=timezone.utc),
            )
        ]


class MissingConversationMessageHistoryService:
    def list_messages(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        del limit, after_turn_index, status
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class InvalidMessageHistoryService:
    def list_messages(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        del conversation_id, limit, after_turn_index, status
        raise ValueError("unsupported status filter")


class CursorMessageHistoryService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def list_messages(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "after_turn_index": after_turn_index,
                "status": status,
            }
        )
        now = datetime(2026, 2, 28, 5, 20, tzinfo=timezone.utc)
        return [
            ConversationMessageRecord(
                turn_index=after_turn_index + i,
                message_id=uuid4(),
                participant_id=uuid4(),
                participant_name="AI(2)",
                participant_kind="ai",
                status="committed",
                message_type="statement",
                content_text=f"message-{i}",
                metadata={},
                created_at=now,
            )
            for i in range(1, limit + 1)
        ]


def test_list_conversation_messages_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: SuccessMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages",
        params={"limit": 20, "after_turn_index": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["turn_index"] == 3
    assert payload[0]["participant_name"] == "AI(2)"


def test_list_conversation_messages_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: MissingConversationMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/messages")

    assert response.status_code == 404


def test_list_conversation_messages_invalid_filter(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: InvalidMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/messages")

    assert response.status_code == 400


def test_list_conversation_messages_page_success(monkeypatch: Any) -> None:
    service = CursorMessageHistoryService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages/page",
        params={"limit": 2, "cursor": "turn:1", "status": "committed"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["turn_index"] == 2
    assert payload["items"][1]["turn_index"] == 3
    assert payload["next_cursor"] == "turn:3"
    assert payload["has_more"] is True
    assert service.calls[0]["limit"] == 3
    assert service.calls[0]["after_turn_index"] == 1
    assert service.calls[0]["status"] == "committed"


def test_list_conversation_messages_page_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: CursorMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages/page",
        params={"cursor": "wrong"},
    )

    assert response.status_code == 400
