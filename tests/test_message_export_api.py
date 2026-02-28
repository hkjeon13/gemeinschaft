"""API tests for conversation message export endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessMessageExportService:
    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> bytes:
        del conversation_id, limit, after_turn_index, status
        return b'{"turn_index":1,"status":"committed"}\n'


class MissingConversationMessageExportService:
    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> bytes:
        del limit, after_turn_index, status
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class CapturingMessageExportService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> bytes:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "after_turn_index": after_turn_index,
                "status": status,
            }
        )
        return b'{"turn_index":1,"status":"committed"}\n'


def test_download_conversation_messages_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_export_service",
        lambda connection: SuccessMessageExportService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = uuid4()

    response = client.get(f"/internal/conversations/{conversation_id}/messages/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert '"status":"committed"' in response.text


def test_download_conversation_messages_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_export_service",
        lambda connection: MissingConversationMessageExportService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/messages/download")

    assert response.status_code == 404


def test_download_conversation_messages_accepts_cursor(monkeypatch: Any) -> None:
    service = CapturingMessageExportService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_export_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages/download",
        params={"cursor": "turn:7", "status": "committed"},
    )

    assert response.status_code == 200
    assert len(service.calls) == 1
    assert service.calls[0]["after_turn_index"] == 7
    assert service.calls[0]["status"] == "committed"


def test_download_conversation_messages_rejects_conflicting_cursor(monkeypatch: Any) -> None:
    service = CapturingMessageExportService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_export_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages/download",
        params={"after_turn_index": 3, "cursor": "turn:9"},
    )

    assert response.status_code == 400
    assert len(service.calls) == 0
