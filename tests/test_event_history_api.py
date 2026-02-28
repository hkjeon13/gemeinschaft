"""API tests for conversation event history endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_history_service import (
    ConversationEventRecord,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessEventHistoryService:
    def list_events(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_seq_no: int = 0,
    ) -> list[ConversationEventRecord]:
        return [
            ConversationEventRecord(
                seq_no=3,
                event_type="turn.pending_approval",
                actor_participant_id=uuid4(),
                message_id=uuid4(),
                payload={"turn_index": 2},
                created_at=datetime(2026, 2, 28, 3, 50, tzinfo=timezone.utc),
            )
        ]


class MissingConversationEventHistoryService:
    def list_events(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_seq_no: int = 0,
    ) -> list[ConversationEventRecord]:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class InvalidEventHistoryService:
    def list_events(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_seq_no: int = 0,
    ) -> list[ConversationEventRecord]:
        raise ValueError("limit must be >= 1")


class CursorEventHistoryService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def list_events(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_seq_no: int = 0,
    ) -> list[ConversationEventRecord]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "after_seq_no": after_seq_no,
            }
        )
        now = datetime(2026, 2, 28, 3, 50, tzinfo=timezone.utc)
        return [
            ConversationEventRecord(
                seq_no=after_seq_no + i,
                event_type="turn.pending_approval",
                actor_participant_id=uuid4(),
                message_id=uuid4(),
                payload={"turn_index": i},
                created_at=now,
            )
            for i in range(1, limit + 1)
        ]


def test_list_conversation_events_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_history_service",
        lambda connection: SuccessEventHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/events",
        params={"limit": 10, "after_seq_no": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["seq_no"] == 3
    assert payload[0]["event_type"] == "turn.pending_approval"


def test_list_conversation_events_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_history_service",
        lambda connection: MissingConversationEventHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/events")

    assert response.status_code == 404


def test_list_conversation_events_invalid_request(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_history_service",
        lambda connection: InvalidEventHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/events")

    assert response.status_code == 400


def test_list_conversation_events_page_success(monkeypatch: Any) -> None:
    service = CursorEventHistoryService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_history_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/events/page",
        params={"limit": 2, "cursor": "seq:0"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["seq_no"] == 1
    assert payload["items"][1]["seq_no"] == 2
    assert payload["next_cursor"] == "seq:2"
    assert payload["has_more"] is True
    assert service.calls[0]["limit"] == 3
    assert service.calls[0]["after_seq_no"] == 0


def test_list_conversation_events_page_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_history_service",
        lambda connection: CursorEventHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/events/page",
        params={"cursor": "bad-cursor"},
    )

    assert response.status_code == 400
