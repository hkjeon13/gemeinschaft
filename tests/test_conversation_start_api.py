"""API tests for automation/manual conversation start endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.conversation_start_service import (
    StartConversationResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class AutomationStartServiceStub:
    def start_conversation(self, payload: Any) -> StartConversationResult:
        return StartConversationResult(
            conversation_id=uuid4(),
            status="active",
            start_trigger="automation",
            created=True,
            event_seq_last=2,
            created_at=datetime(2026, 2, 27, 19, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 2, 27, 19, 0, tzinfo=timezone.utc),
        )


class AutomationDuplicateServiceStub:
    def start_conversation(self, payload: Any) -> StartConversationResult:
        del payload
        return StartConversationResult(
            conversation_id=uuid4(),
            status="active",
            start_trigger="automation",
            created=False,
            event_seq_last=2,
            created_at=datetime(2026, 2, 27, 19, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 2, 27, 19, 0, tzinfo=timezone.utc),
        )


class ManualStartServiceStub:
    def start_conversation(self, payload: Any) -> StartConversationResult:
        del payload
        return StartConversationResult(
            conversation_id=uuid4(),
            status="active",
            start_trigger="human",
            created=True,
            event_seq_last=2,
            created_at=datetime(2026, 2, 27, 19, 5, tzinfo=timezone.utc),
            started_at=datetime(2026, 2, 27, 19, 5, tzinfo=timezone.utc),
        )


def test_start_automation_conversation_endpoint_success(monkeypatch: Any) -> None:
    connection = DummyConnection()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: connection)
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_conversation_start_service",
        lambda conn: AutomationStartServiceStub(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        "/internal/conversations/start/automation",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "title": "Automated Summary Run",
            "objective": "Generate daily automated discussion",
            "automation_run_id": "run-001",
        },
    )

    assert response.status_code == 201
    assert response.json()["start_trigger"] == "automation"
    assert response.json()["created"] is True
    assert connection.closed is True


def test_start_automation_conversation_endpoint_duplicate(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_conversation_start_service",
        lambda conn: AutomationDuplicateServiceStub(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        "/internal/conversations/start/automation",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "title": "Automated Summary Run",
            "objective": "Generate daily automated discussion",
            "automation_run_id": "run-001",
        },
    )

    assert response.status_code == 200
    assert response.json()["created"] is False


def test_start_manual_conversation_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_conversation_start_service",
        lambda conn: ManualStartServiceStub(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        "/internal/conversations/start/manual",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "title": "Manual Discussion",
            "objective": "Kick off human-led discussion",
        },
    )

    assert response.status_code == 201
    assert response.json()["start_trigger"] == "human"
    assert response.json()["created"] is True
