"""Scheduler auth scope and role guard tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.scheduler import app as scheduler_app_module
from services.scheduler.repository import (
    AutomationRunRecord,
    AutomationTemplateDetailRecord,
    AutomationTemplateRecord,
    AutomationTemplateListRecord,
    CreateAutomationTemplateResult,
    SetTemplateEnabledResult,
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
            created_at=datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc),
        )

    def list_templates(
        self,
        *,
        tenant_id: Any,
        workspace_id: Any,
        include_disabled: bool,
        limit: int,
        before_updated_at: datetime | None = None,
        before_template_id: Any | None = None,
    ) -> list[AutomationTemplateListRecord]:
        del include_disabled, limit, before_updated_at, before_template_id
        ts = datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)
        return [
            AutomationTemplateListRecord(
                template_id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="Hourly",
                rrule="FREQ=HOURLY;INTERVAL=1",
                enabled=True,
                created_at=ts,
                updated_at=ts,
            )
        ]


class SchedulerRunRepoStub:
    trigger_calls = 0
    template_tenant_id = uuid4()
    template_workspace_id = uuid4()

    def get_template(self, template_id: Any) -> AutomationTemplateRecord:
        return AutomationTemplateRecord(
            template_id=template_id,
            tenant_id=self.template_tenant_id,
            workspace_id=self.template_workspace_id,
            name="Hourly",
            conversation_objective="Generate insights",
            participants=["ai_1"],
            enabled=True,
            metadata={},
        )

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        type(self).trigger_calls += 1
        return TriggerAutomationRunResult(
            run_id=11,
            template_id=payload.template_id,
            scheduled_for=payload.scheduled_for,
            idempotency_key="idemp",
            status="triggered",
            triggered_at=datetime(2026, 2, 28, 6, 1, tzinfo=timezone.utc),
        )


class SchedulerTemplateMutationRepoStub:
    set_enabled_calls = 0
    update_calls = 0
    template_tenant_id = uuid4()
    template_workspace_id = uuid4()

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        ts = datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)
        return AutomationTemplateDetailRecord(
            template_id=template_id,
            tenant_id=self.template_tenant_id,
            workspace_id=self.template_workspace_id,
            name="Hourly",
            conversation_objective="Generate insights",
            rrule="FREQ=HOURLY;INTERVAL=1",
            participants=["ai_1"],
            enabled=True,
            metadata={},
            created_at=ts,
            updated_at=ts,
        )

    def set_template_enabled(
        self, *, template_id: Any, enabled: bool
    ) -> SetTemplateEnabledResult:
        del template_id, enabled
        type(self).set_enabled_calls += 1
        return SetTemplateEnabledResult(
            template_id=uuid4(),
            enabled=False,
            updated_at=datetime(2026, 2, 28, 6, 10, tzinfo=timezone.utc),
        )

    def update_template(self, payload: Any) -> AutomationTemplateDetailRecord:
        type(self).update_calls += 1
        ts = datetime(2026, 2, 28, 6, 20, tzinfo=timezone.utc)
        return AutomationTemplateDetailRecord(
            template_id=payload.template_id,
            tenant_id=self.template_tenant_id,
            workspace_id=self.template_workspace_id,
            name=payload.name or "Hourly",
            conversation_objective=payload.conversation_objective or "Generate insights",
            rrule=payload.rrule or "FREQ=HOURLY;INTERVAL=1",
            participants=payload.participants or ["ai_1"],
            enabled=True,
            metadata=payload.metadata or {},
            created_at=ts,
            updated_at=ts,
        )


class SchedulerTemplateRunsRepoStub:
    list_runs_calls = 0
    template_tenant_id = uuid4()
    template_workspace_id = uuid4()

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        ts = datetime(2026, 2, 28, 6, 0, tzinfo=timezone.utc)
        return AutomationTemplateDetailRecord(
            template_id=template_id,
            tenant_id=self.template_tenant_id,
            workspace_id=self.template_workspace_id,
            name="Hourly",
            conversation_objective="Generate insights",
            rrule="FREQ=HOURLY;INTERVAL=1",
            participants=["ai_1"],
            enabled=True,
            metadata={},
            created_at=ts,
            updated_at=ts,
        )

    def list_runs(
        self,
        *,
        template_id: Any,
        limit: int,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        del template_id, limit, before_scheduled_for, before_run_id
        type(self).list_runs_calls += 1
        return []


def test_create_template_rejects_viewer_role(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/automation/templates",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
        },
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "name": "Hourly",
            "conversation_objective": "Generate insights",
            "rrule": "FREQ=HOURLY;INTERVAL=1",
        },
    )

    assert response.status_code == 403


def test_create_template_rejects_scope_mismatch(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    payload_tenant = uuid4()
    payload_workspace = uuid4()
    auth_tenant = uuid4()
    auth_workspace = uuid4()

    response = client.post(
        "/internal/automation/templates",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(auth_tenant),
            "x-auth-workspace-id": str(auth_workspace),
        },
        json={
            "tenant_id": str(payload_tenant),
            "workspace_id": str(payload_workspace),
            "name": "Hourly",
            "conversation_objective": "Generate insights",
            "rrule": "FREQ=HOURLY;INTERVAL=1",
        },
    )

    assert response.status_code == 403


def test_list_templates_allows_viewer_with_matching_scope(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    tenant_id = uuid4()
    workspace_id = uuid4()

    response = client.get(
        "/internal/automation/templates",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
        params={
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1


def test_list_templates_page_allows_viewer_with_matching_scope(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    tenant_id = uuid4()
    workspace_id = uuid4()

    response = client.get(
        "/internal/automation/templates/page",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
        params={
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1


def test_trigger_run_rejects_scope_mismatch_before_write(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerRunRepoStub.trigger_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRunRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/trigger",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-28T06:00:00Z",
        },
    )

    assert response.status_code == 403
    assert SchedulerRunRepoStub.trigger_calls == 0


def test_execute_run_rejects_scope_mismatch_before_write(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerRunRepoStub.trigger_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRunRepoStub(),
    )
    monkeypatch.setattr(scheduler_app_module, "_build_orchestrator_client", lambda: None)
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-28T06:00:00Z",
        },
    )

    assert response.status_code == 403
    assert SchedulerRunRepoStub.trigger_calls == 0


def test_execute_batch_returns_forbidden_scope_error_code(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerRunRepoStub.trigger_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRunRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute-batch",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        json={
            "template_ids": [str(uuid4())],
            "scheduled_for": "2026-02-28T06:00:00Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 1
    assert payload["succeeded"] == 0
    assert payload["failed"] == 1
    assert payload["items"][0]["error_code"] == "forbidden_scope"
    assert SchedulerRunRepoStub.trigger_calls == 0


def test_set_enabled_rejects_scope_mismatch_before_write(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerTemplateMutationRepoStub.set_enabled_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerTemplateMutationRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.patch(
        f"/internal/automation/templates/{uuid4()}/enabled",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        json={"enabled": False},
    )

    assert response.status_code == 403
    assert SchedulerTemplateMutationRepoStub.set_enabled_calls == 0


def test_patch_template_rejects_scope_mismatch_before_write(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerTemplateMutationRepoStub.update_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerTemplateMutationRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.patch(
        f"/internal/automation/templates/{uuid4()}",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        json={"name": "Renamed"},
    )

    assert response.status_code == 403
    assert SchedulerTemplateMutationRepoStub.update_calls == 0


def test_list_runs_rejects_scope_mismatch_before_query(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerTemplateRunsRepoStub.list_runs_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerTemplateRunsRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get(
        f"/internal/automation/templates/{uuid4()}/runs",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert SchedulerTemplateRunsRepoStub.list_runs_calls == 0


def test_list_runs_page_rejects_scope_mismatch_before_query(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    SchedulerTemplateRunsRepoStub.list_runs_calls = 0
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerTemplateRunsRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get(
        f"/internal/automation/templates/{uuid4()}/runs/page",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert SchedulerTemplateRunsRepoStub.list_runs_calls == 0
