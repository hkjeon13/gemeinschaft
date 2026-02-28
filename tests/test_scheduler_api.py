"""API tests for scheduler service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.scheduler import app as scheduler_app_module
from services.scheduler.orchestrator_client import (
    OrchestratorCallError,
    StartAutomationConversationClientInput,
    StartAutomationConversationClientResult,
)
from services.scheduler.repository import (
    AutomationRunNotFoundError,
    AutomationRunRecord,
    AutomationTemplateDetailRecord,
    AutomationTemplateListRecord,
    AutomationTemplateRecord,
    CreateAutomationTemplateResult,
    SetTemplateEnabledResult,
    TemplateNotFoundError,
    TriggerAutomationRunResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SchedulerRepoStub:
    mark_failed_calls: list[dict[str, Any]] = []

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

    def get_template(self, template_id: Any) -> AutomationTemplateRecord:
        return AutomationTemplateRecord(
            template_id=template_id,
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            name="Hourly default",
            conversation_objective="Generate periodic insights",
            participants=["ai_1", "ai_2"],
            enabled=True,
            metadata={},
        )

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        now = datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc)
        return AutomationTemplateDetailRecord(
            template_id=template_id,
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            name="Hourly default",
            conversation_objective="Generate periodic insights",
            rrule="FREQ=HOURLY;INTERVAL=1",
            participants=["ai_1", "ai_2"],
            enabled=True,
            metadata={"source": "test"},
            created_at=now,
            updated_at=now,
        )

    def list_runs(
        self,
        template_id: Any,
        limit: int,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        del limit, before_scheduled_for, before_run_id
        return [
            AutomationRunRecord(
                run_id=11,
                template_id=template_id,
                scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
                idempotency_key="abc123",
                status="triggered",
                triggered_at=datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc),
                metadata={"trigger": "cron"},
            )
        ]

    def get_run(self, run_id: int) -> AutomationRunRecord:
        status = "failed" if run_id == 77 else "triggered"
        return AutomationRunRecord(
            run_id=run_id,
            template_id=uuid4(),
            scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
            idempotency_key=f"run-{run_id}",
            status=status,
            triggered_at=datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc),
            metadata={"source": "test"},
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
        now = datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc)
        return [
            AutomationTemplateListRecord(
                template_id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="Hourly default",
                rrule="FREQ=HOURLY;INTERVAL=1",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        ]

    def set_template_enabled(self, *, template_id: Any, enabled: bool) -> SetTemplateEnabledResult:
        return SetTemplateEnabledResult(
            template_id=template_id,
            enabled=enabled,
            updated_at=datetime(2026, 2, 27, 18, 10, tzinfo=timezone.utc),
        )

    def update_template(self, payload: Any) -> AutomationTemplateDetailRecord:
        if (
            payload.name is None
            and payload.conversation_objective is None
            and payload.rrule is None
            and payload.participants is None
            and payload.metadata is None
        ):
            raise ValueError("at least one update field must be provided")
        now = datetime(2026, 2, 27, 18, 20, tzinfo=timezone.utc)
        return AutomationTemplateDetailRecord(
            template_id=payload.template_id,
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            name=payload.name or "Hourly default",
            conversation_objective=payload.conversation_objective
            or "Generate periodic insights",
            rrule=payload.rrule or "FREQ=HOURLY;INTERVAL=1",
            participants=payload.participants or ["ai_1", "ai_2"],
            enabled=True,
            metadata=payload.metadata or {},
            created_at=now,
            updated_at=now,
        )

    def mark_run_failed(
        self,
        *,
        run_id: int,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.mark_failed_calls.append(
            {
                "run_id": run_id,
                "error_message": error_message,
                "metadata": metadata or {},
            }
        )


class TriggerCountingRepoStub(SchedulerRepoStub):
    trigger_calls = 0

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        type(self).trigger_calls += 1
        return super().trigger_run(payload)


class CursorTemplateRepoStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._ts1 = datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc)
        self._ts2 = datetime(2026, 2, 27, 19, 0, tzinfo=timezone.utc)
        self._id1 = uuid4()
        self._id2 = uuid4()

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
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "include_disabled": include_disabled,
                "limit": limit,
                "before_updated_at": before_updated_at,
                "before_template_id": before_template_id,
            }
        )
        rows = [
            AutomationTemplateListRecord(
                template_id=self._id1,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="A",
                rrule="FREQ=HOURLY;INTERVAL=1",
                enabled=True,
                created_at=self._ts1,
                updated_at=self._ts1,
            ),
            AutomationTemplateListRecord(
                template_id=self._id2,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="B",
                rrule="FREQ=WEEKLY;BYDAY=FR",
                enabled=True,
                created_at=self._ts2,
                updated_at=self._ts2,
            ),
        ]
        if before_updated_at is not None and before_template_id is not None:
            return rows[1:][:limit]
        return rows[:limit]


class CursorRunRepoStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._tenant_id = uuid4()
        self._workspace_id = uuid4()
        self._s1 = datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc)
        self._s2 = datetime(2026, 2, 27, 17, 0, tzinfo=timezone.utc)

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        return AutomationTemplateDetailRecord(
            template_id=template_id,
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            name="Hourly default",
            conversation_objective="Generate periodic insights",
            rrule="FREQ=HOURLY;INTERVAL=1",
            participants=["ai_1"],
            enabled=True,
            metadata={},
            created_at=self._s1,
            updated_at=self._s1,
        )

    def list_runs(
        self,
        *,
        template_id: Any,
        limit: int,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        self.calls.append(
            {
                "template_id": template_id,
                "limit": limit,
                "before_scheduled_for": before_scheduled_for,
                "before_run_id": before_run_id,
            }
        )
        rows = [
            AutomationRunRecord(
                run_id=11,
                template_id=template_id,
                scheduled_for=self._s1,
                idempotency_key="r11",
                status="triggered",
                triggered_at=self._s1,
                metadata={},
            ),
            AutomationRunRecord(
                run_id=10,
                template_id=template_id,
                scheduled_for=self._s2,
                idempotency_key="r10",
                status="triggered",
                triggered_at=self._s2,
                metadata={},
            ),
        ]
        if before_scheduled_for is not None and before_run_id is not None:
            return rows[1:][:limit]
        return rows[:limit]


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

    def get_template(self, template_id: Any) -> AutomationTemplateRecord:
        return AutomationTemplateRecord(
            template_id=template_id,
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            name="Hourly default",
            conversation_objective="Generate periodic insights",
            participants=["ai_1", "ai_2"],
            enabled=True,
            metadata={},
        )

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        raise TemplateNotFoundError(f"Automation template {template_id} not found")

    def list_runs(
        self,
        template_id: Any,
        limit: int,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        del template_id, limit, before_scheduled_for, before_run_id
        return []

    def get_run(self, run_id: int) -> AutomationRunRecord:
        return AutomationRunRecord(
            run_id=run_id,
            template_id=uuid4(),
            scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
            idempotency_key="dup",
            status="triggered",
            triggered_at=datetime(2026, 2, 27, 18, 1, tzinfo=timezone.utc),
            metadata={},
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
        del tenant_id, workspace_id, include_disabled, limit, before_updated_at, before_template_id
        return []

    def set_template_enabled(self, *, template_id: Any, enabled: bool) -> SetTemplateEnabledResult:
        raise TemplateNotFoundError(f"Automation template {template_id} not found")

    def update_template(self, payload: Any) -> AutomationTemplateDetailRecord:
        raise TemplateNotFoundError(f"Automation template {payload.template_id} not found")

    def mark_run_failed(
        self,
        *,
        run_id: int,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del run_id, error_message, metadata


class NotFoundRepoStub:
    def create_template(self, payload: Any) -> CreateAutomationTemplateResult:
        del payload
        raise AssertionError("not used")

    def trigger_run(self, payload: Any) -> TriggerAutomationRunResult:
        raise TemplateNotFoundError(f"Automation template {payload.template_id} not found")

    def get_template(self, template_id: Any) -> AutomationTemplateRecord:
        raise TemplateNotFoundError(f"Automation template {template_id} not found")

    def get_template_detail(self, template_id: Any) -> AutomationTemplateDetailRecord:
        raise TemplateNotFoundError(f"Automation template {template_id} not found")

    def list_runs(
        self,
        template_id: Any,
        limit: int,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        del template_id, limit, before_scheduled_for, before_run_id
        return []

    def get_run(self, run_id: int) -> AutomationRunRecord:
        raise AutomationRunNotFoundError(f"Automation run {run_id} not found")

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
        del tenant_id, workspace_id, include_disabled, limit, before_updated_at, before_template_id
        return []

    def set_template_enabled(self, *, template_id: Any, enabled: bool) -> SetTemplateEnabledResult:
        del enabled
        raise TemplateNotFoundError(f"Automation template {template_id} not found")

    def update_template(self, payload: Any) -> AutomationTemplateDetailRecord:
        raise TemplateNotFoundError(f"Automation template {payload.template_id} not found")

    def mark_run_failed(
        self,
        *,
        run_id: int,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del run_id, error_message, metadata


class OrchestratorClientStub:
    def start_automation_conversation(
        self, payload: Any
    ) -> StartAutomationConversationClientResult:
        return StartAutomationConversationClientResult(
            conversation_id=uuid4(),
            status="active",
            start_trigger="automation",
            created=True,
            event_seq_last=2,
        )


class FailingOrchestratorClientStub:
    def start_automation_conversation(
        self, payload: Any
    ) -> StartAutomationConversationClientResult:
        raise OrchestratorCallError("upstream failed")


class CapturingOrchestratorClientStub:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def start_automation_conversation(
        self, payload: Any
    ) -> StartAutomationConversationClientResult:
        self.calls.append(payload)
        return StartAutomationConversationClientResult(
            conversation_id=uuid4(),
            status="active",
            start_trigger="automation",
            created=True,
            event_seq_last=2,
        )


def test_build_orchestrator_client_prefers_override_token(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "base-token")
    monkeypatch.setenv("ORCHESTRATOR_INTERNAL_API_TOKEN", "override-token")
    monkeypatch.setenv("SCHEDULER_INTERNAL_ROLE", "operator")
    monkeypatch.setenv("SCHEDULER_INTERNAL_PRINCIPAL_ID", "scheduler-01")

    client = scheduler_app_module._build_orchestrator_client()

    assert client is not None
    headers = client._build_headers(
        StartAutomationConversationClientInput(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            title="t",
            objective="o",
            automation_template_id=uuid4(),
            automation_run_id="11",
            scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
            participants=[],
            metadata={},
        )
    )
    assert headers["x-internal-api-token"] == "override-token"
    assert headers["x-internal-role"] == "operator"
    assert headers["x-internal-principal-id"] == "scheduler-01"


def test_build_orchestrator_client_falls_back_to_internal_token(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "base-token")
    monkeypatch.delenv("ORCHESTRATOR_INTERNAL_API_TOKEN", raising=False)

    client = scheduler_app_module._build_orchestrator_client()

    assert client is not None
    headers = client._build_headers(
        StartAutomationConversationClientInput(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            title="t",
            objective="o",
            automation_template_id=uuid4(),
            automation_run_id="11",
            scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
            participants=[],
            metadata={},
        )
    )
    assert headers["x-internal-api-token"] == "base-token"
    assert headers["x-internal-role"] == "system"
    assert headers["x-internal-principal-id"] == "scheduler-service"


def test_build_orchestrator_client_rejects_negative_retry(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("ORCHESTRATOR_HTTP_MAX_RETRIES", "-1")

    try:
        scheduler_app_module._build_orchestrator_client()
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "ORCHESTRATOR_HTTP_MAX_RETRIES must be >= 0"
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected HTTPException for negative retry config")


def test_build_orchestrator_client_rejects_invalid_timeout(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("ORCHESTRATOR_HTTP_TIMEOUT_SECONDS", "NaN?")

    try:
        scheduler_app_module._build_orchestrator_client()
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "invalid orchestrator http retry/timeout configuration"
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected HTTPException for invalid timeout config")


def test_execute_run_invalid_client_config_fails_before_trigger(monkeypatch: Any) -> None:
    TriggerCountingRepoStub.trigger_calls = 0
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("ORCHESTRATOR_HTTP_MAX_RETRIES", "-1")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: TriggerCountingRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 500
    assert TriggerCountingRepoStub.trigger_calls == 0


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


def test_list_templates_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())

    response = client.get(
        "/internal/automation/templates",
        params={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "include_disabled": "false",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["tenant_id"] == tenant_id
    assert payload[0]["workspace_id"] == workspace_id
    assert payload[0]["name"] == "Hourly default"


def test_list_templates_page_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    repo = CursorTemplateRepoStub()
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: repo,
    )
    client = TestClient(scheduler_app_module.app)
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())

    first = client.get(
        "/internal/automation/templates/page",
        params={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "limit": 1,
        },
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["name"] == "A"
    assert first_payload["next_cursor"] is not None
    assert "Z|" in first_payload["next_cursor"]
    assert first_payload["has_more"] is True
    assert repo.calls[0]["limit"] == 2

    second = client.get(
        "/internal/automation/templates/page",
        params={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "limit": 1,
            "cursor": first_payload["next_cursor"],
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["name"] == "B"
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False
    assert repo.calls[1]["before_updated_at"] is not None
    assert repo.calls[1]["before_template_id"] is not None


def test_list_templates_page_endpoint_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: CursorTemplateRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get(
        "/internal/automation/templates/page",
        params={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "cursor": "bad-cursor",
        },
    )

    assert response.status_code == 400


def test_get_template_detail_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.get(f"/internal/automation/templates/{template_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_id"] == template_id
    assert payload["rrule"] == "FREQ=HOURLY;INTERVAL=1"
    assert payload["enabled"] is True


def test_get_template_detail_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: NotFoundRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get(f"/internal/automation/templates/{uuid4()}")

    assert response.status_code == 404


def test_patch_template_enabled_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.patch(
        f"/internal/automation/templates/{template_id}/enabled",
        json={"enabled": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_id"] == template_id
    assert payload["enabled"] is False


def test_patch_template_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.patch(
        f"/internal/automation/templates/{template_id}",
        json={
            "name": "Updated",
            "conversation_objective": "Updated objective",
            "rrule": "FREQ=WEEKLY;BYDAY=FR",
            "participants": ["ai_1"],
            "metadata": {"v": 2},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_id"] == template_id
    assert payload["name"] == "Updated"
    assert payload["participants"] == ["ai_1"]


def test_patch_template_endpoint_empty_payload(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.patch(f"/internal/automation/templates/{uuid4()}", json={})

    assert response.status_code == 400


def test_execute_batch_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: OrchestratorClientStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute-batch",
        json={
            "template_ids": [str(uuid4()), str(uuid4())],
            "scheduled_for": "2026-02-27T18:00:00Z",
            "metadata": {"trigger": "batch"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 2
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0
    assert len(payload["items"]) == 2
    assert payload["items"][0]["success"] is True


def test_execute_batch_endpoint_rejects_duplicate_template_ids() -> None:
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.post(
        "/internal/scheduler/runs/execute-batch",
        json={
            "template_ids": [template_id, template_id],
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 422


def test_execute_batch_returns_orchestrator_config_error(monkeypatch: Any) -> None:
    TriggerCountingRepoStub.trigger_calls = 0
    monkeypatch.setenv("CONVERSATION_ORCHESTRATOR_BASE_URL", "http://orchestrator:8001")
    monkeypatch.setenv("ORCHESTRATOR_HTTP_MAX_RETRIES", "-1")
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: TriggerCountingRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute-batch",
        json={
            "template_ids": [str(uuid4())],
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 1
    assert payload["failed"] == 1
    assert payload["items"][0]["error_code"] == "orchestrator_config_error"
    assert TriggerCountingRepoStub.trigger_calls == 0


def test_get_run_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get("/internal/scheduler/runs/11")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == 11
    assert payload["status"] == "triggered"


def test_get_run_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: NotFoundRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get("/internal/scheduler/runs/999")

    assert response.status_code == 404


def test_retry_run_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: OrchestratorClientStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/77/retry",
        json={"metadata": {"retry": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_run_id"] == 77
    assert payload["conversation_started"] is True


def test_retry_run_endpoint_conflict_when_not_failed(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post("/internal/scheduler/runs/11/retry", json={})

    assert response.status_code == 409


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


def test_execute_run_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: OrchestratorClientStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
            "metadata": {"trigger": "cron"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "triggered"
    assert payload["conversation_started"] is True
    assert payload["conversation_id"] is not None
    assert payload["conversation_created"] is True


def test_execute_run_forwards_request_id_to_orchestrator(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    capturing_client = CapturingOrchestratorClientStub()
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: capturing_client,
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        headers={"x-request-id": "req-execute-1"},
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 200
    assert len(capturing_client.calls) == 1
    assert capturing_client.calls[0].request_id == "req-execute-1"


def test_execute_run_endpoint_without_auto_start(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: OrchestratorClientStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
            "auto_start_conversation": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_started"] is False
    assert payload["conversation_id"] is None


def test_execute_run_endpoint_orchestrator_not_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: None,
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_started"] is False


def test_execute_batch_forwards_request_id(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    capturing_client = CapturingOrchestratorClientStub()
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: capturing_client,
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute-batch",
        headers={"x-request-id": "req-batch-1"},
        json={
            "template_ids": [str(uuid4())],
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 200
    assert len(capturing_client.calls) == 1
    assert capturing_client.calls[0].request_id == "req-batch-1"


def test_execute_run_endpoint_orchestrator_failure(monkeypatch: Any) -> None:
    SchedulerRepoStub.mark_failed_calls.clear()
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_orchestrator_client",
        lambda: FailingOrchestratorClientStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/execute",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 502
    assert len(SchedulerRepoStub.mark_failed_calls) == 1
    assert SchedulerRepoStub.mark_failed_calls[0]["run_id"] == 11


def test_preview_run_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.post(
        "/internal/scheduler/runs/preview",
        json={
            "template_id": template_id,
            "scheduled_for": "2026-02-27T18:00:31Z",
            "metadata": {"source": "preview"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_id"] == template_id
    assert payload["scheduled_for"].endswith("18:00:00Z")
    assert payload["idempotency_key"]
    assert len(payload["participants"]) == 2
    assert payload["start_payload"]["automation_run_id"] == "{run_id}"


def test_preview_run_endpoint_template_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: NotFoundRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.post(
        "/internal/scheduler/runs/preview",
        json={
            "template_id": str(uuid4()),
            "scheduled_for": "2026-02-27T18:00:00Z",
        },
    )

    assert response.status_code == 404


def test_list_template_runs_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: SchedulerRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    response = client.get(
        f"/internal/automation/templates/{template_id}/runs",
        params={"limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["run_id"] == 11
    assert payload[0]["status"] == "triggered"


def test_list_template_runs_page_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    repo = CursorRunRepoStub()
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: repo,
    )
    client = TestClient(scheduler_app_module.app)
    template_id = str(uuid4())

    first = client.get(
        f"/internal/automation/templates/{template_id}/runs/page",
        params={"limit": 1},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["run_id"] == 11
    assert first_payload["next_cursor"] is not None
    assert "Z|" in first_payload["next_cursor"]
    assert first_payload["has_more"] is True
    assert repo.calls[0]["limit"] == 2

    second = client.get(
        f"/internal/automation/templates/{template_id}/runs/page",
        params={"limit": 1, "cursor": first_payload["next_cursor"]},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["run_id"] == 10
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False
    assert repo.calls[1]["before_scheduled_for"] is not None
    assert repo.calls[1]["before_run_id"] == 11


def test_list_template_runs_page_endpoint_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(scheduler_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        scheduler_app_module,
        "_build_repository",
        lambda connection: CursorRunRepoStub(),
    )
    client = TestClient(scheduler_app_module.app)

    response = client.get(
        f"/internal/automation/templates/{uuid4()}/runs/page",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 400
