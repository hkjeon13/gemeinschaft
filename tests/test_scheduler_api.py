"""API tests for scheduler service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.scheduler import app as scheduler_app_module
from services.scheduler.repository import (
    CreateAutomationTemplateResult,
    TemplateNotFoundError,
    TriggerAutomationRunResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SchedulerRepoStub:
    def create_template(self, payload: Any) -> CreateAutomationTemplateResult:
        return CreateAutomationTemplateResult(
            template_id=uuid4(),
            created_at=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
        )

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        return TriggerAutomationRunResult(
            run_id=11,
            template_id=payload.template_id,
            scheduled_for=payload.scheduled_for,
            idempotency_key="abc123",
            status="triggered",
            triggered_at=datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc),
        )


class DuplicateRepoStub:
    def create_template(self, payload: Any) -> CreateAutomationTemplateResult:
        del payload
        raise AssertionError("not used")

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        return TriggerAutomationRunResult(
            run_id=11,
            template_id=payload.template_id,
            scheduled_for=payload.scheduled_for,
            idempotency_key="abc123",
            status="duplicate",
            triggered_at=datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc),
        )


class NotFoundRepoStub:
    def create_template(self, payload: Any) -> CreateAutomationTemplateResult:
        del payload
        raise AssertionError("not used")

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        raise TemplateNotFoundError(f"Automation template {payload.template_id} not found")


def test_create_template_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/automation/templates",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "name": "Hourly default",
            "conversation_objective": "Generate periodic insights",
            "rrule": "FREQ=HOURLY;INTERVAL=1",
            "participants": ["ai_1", "ai_2"],
            "enabled": True,
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["template_id"]
    assert payload["created_at"]


def test_trigger_run_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.post(
        "/internal/scheduler/runs/trigger",
        json={
            "template_id": template_id,
            "scheduled_for": "2026-02-27T18:00:00Z",
            "metadata": {"trigger": "cron"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "triggered"
    assert payload["run_id"] == 11


def test_trigger_run_endpoint_duplicate(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: DuplicateRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.post(
        "/internal/scheduler/runs/trigger",
        json={
            "template_id": template_id,
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"


def test_trigger_run_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: NotFoundRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/trigger",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 404
