"""API tests for conversation event export endpoint."""

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


class SuccessEventExportService:
    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_seq_no: int = 0,
    ) -> bytes:
        return b'{"seq_no":1,"event_type":"conversation.started"}\n'


class MissingConversationEventExportService:
    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_seq_no: int = 0,
    ) -> bytes:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class CapturingEventExportService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def export_jsonl(
        self,
        *,
        conversation_id: Any,
        limit: int = 5000,
        after_seq_no: int = 0,
    ) -> bytes:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "after_seq_no": after_seq_no,
            }
        )
        return b'{"seq_no":1,"event_type":"conversation.started"}\n'


def test_download_conversation_events_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_export_service",
        lambda connection: SuccessEventExportService(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = uuid4()

    response = client.get(f"/internal/conversations/{conversation_id}/events/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert '"event_type":"conversation.started"' in response.text


def test_download_conversation_events_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_export_service",
        lambda connection: MissingConversationEventExportService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/events/download")

    assert response.status_code == 404


def test_download_conversation_events_accepts_cursor(monkeypatch: Any) -> None:
    service = CapturingEventExportService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_export_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/events/download",
        params={"cursor": "seq:4"},
    )

    assert response.status_code == 200
    assert len(service.calls) == 1
    assert service.calls[0]["after_seq_no"] == 4


def test_download_conversation_events_rejects_conflicting_cursor(monkeypatch: Any) -> None:
    service = CapturingEventExportService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_event_export_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/events/download",
        params={"after_seq_no": 2, "cursor": "seq:5"},
    )

    assert response.status_code == 400
    assert len(service.calls) == 0
