"""Scheduler service app."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.scheduler.orchestrator_client import (
    OrchestratorCallError,
    OrchestratorClient,
    StartAutomationConversationClientInput,
    StartAutomationConversationClientResult,
)
from services.scheduler.repository import (
    AutomationRunNotFoundError,
    AutomationTemplateRecord,
    AutomationTemplateDetailRecord,
    AutomationTemplateListRecord,
    AutomationRunRecord,
    build_idempotency_key,
    CreateAutomationTemplateInput,
    CreateAutomationTemplateResult,
    normalize_scheduled_for,
    SetTemplateEnabledResult,
    SchedulerRepository,
    TemplateNotFoundError,
    TriggerAutomationRunInput,
    TriggerAutomationRunResult,
    UpdateAutomationTemplateInput,
)
from services.shared.app_factory import build_service_app
from services.shared.auth import enforce_role, enforce_scope, get_auth_context
from services.shared.db import get_db_connection

app = build_service_app("scheduler")


class CreateAutomationTemplateRequest(BaseModel):
    tenant_id: UUID
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=120)
    conversation_objective: str = Field(min_length=1)
    rrule: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CreateAutomationTemplateResponse(BaseModel):
    template_id: UUID
    created_at: datetime


class AutomationTemplateSummaryResponse(BaseModel):
    template_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    rrule: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AutomationTemplatePageResponse(BaseModel):
    items: list[AutomationTemplateSummaryResponse]
    next_cursor: str | None
    has_more: bool


class AutomationTemplateDetailResponse(BaseModel):
    template_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    conversation_objective: str
    rrule: str
    participants: list[str]
    enabled: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class UpdateTemplateEnabledRequest(BaseModel):
    enabled: bool

    model_config = ConfigDict(extra="forbid")


class UpdateTemplateEnabledResponse(BaseModel):
    template_id: UUID
    enabled: bool
    updated_at: datetime


class UpdateAutomationTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    conversation_objective: str | None = Field(default=None, min_length=1)
    rrule: str | None = Field(default=None, min_length=1)
    participants: list[str] | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class TriggerAutomationRunRequest(BaseModel):
    template_id: UUID
    scheduled_for: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class TriggerAutomationRunResponse(BaseModel):
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime


class ExecuteAutomationRunRequest(BaseModel):
    template_id: UUID
    scheduled_for: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_start_conversation: bool = True

    model_config = ConfigDict(extra="forbid")


class ExecuteAutomationRunResponse(BaseModel):
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime
    conversation_started: bool
    conversation_id: UUID | None = None
    conversation_created: bool | None = None


class BatchExecuteAutomationRunsRequest(BaseModel):
    template_ids: list[UUID] = Field(min_length=1, max_length=100)
    scheduled_for: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_start_conversation: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("template_ids")
    @classmethod
    def _validate_unique_template_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("template_ids must not contain duplicates")
        return value


class BatchExecuteAutomationRunItemResponse(BaseModel):
    template_id: UUID
    success: bool
    run_id: int | None
    status: str | None
    conversation_started: bool
    conversation_id: UUID | None = None
    conversation_created: bool | None = None
    error_code: str | None = None
    error_message: str | None = None


class BatchExecuteAutomationRunsResponse(BaseModel):
    processed: int
    succeeded: int
    failed: int
    items: list[BatchExecuteAutomationRunItemResponse]


class RetryAutomationRunRequest(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_start_conversation: bool = True

    model_config = ConfigDict(extra="forbid")


class RetryAutomationRunResponse(BaseModel):
    source_run_id: int
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime
    conversation_started: bool
    conversation_id: UUID | None = None
    conversation_created: bool | None = None


class PreviewAutomationRunRequest(BaseModel):
    template_id: UUID
    scheduled_for: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PreviewAutomationRunResponse(BaseModel):
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    participants: list[dict[str, Any]]
    start_payload: dict[str, Any]


class AutomationRunResponse(BaseModel):
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime
    metadata: dict[str, Any]


class AutomationRunPageResponse(BaseModel):
    items: list[AutomationRunResponse]
    next_cursor: str | None
    has_more: bool


def _connect() -> Any:
    try:
        return get_db_connection("scheduler")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_repository(connection: Any) -> SchedulerRepository:
    return SchedulerRepository(connection)


def _build_orchestrator_client() -> OrchestratorClient | None:
    base_url = os.getenv("CONVERSATION_ORCHESTRATOR_BASE_URL")
    if not base_url:
        return None
    internal_api_token = os.getenv("ORCHESTRATOR_INTERNAL_API_TOKEN") or os.getenv(
        "INTERNAL_API_TOKEN"
    )
    role = os.getenv("SCHEDULER_INTERNAL_ROLE", "system")
    principal_id = os.getenv("SCHEDULER_INTERNAL_PRINCIPAL_ID", "scheduler-service")
    try:
        timeout_seconds = float(os.getenv("ORCHESTRATOR_HTTP_TIMEOUT_SECONDS", "10"))
        max_retries = int(os.getenv("ORCHESTRATOR_HTTP_MAX_RETRIES", "0"))
        retry_backoff_seconds = float(
            os.getenv("ORCHESTRATOR_HTTP_RETRY_BACKOFF_SECONDS", "0.2")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="invalid orchestrator http retry/timeout configuration",
        ) from exc
    if timeout_seconds <= 0:
        raise HTTPException(
            status_code=500,
            detail="ORCHESTRATOR_HTTP_TIMEOUT_SECONDS must be > 0",
        )
    if max_retries < 0:
        raise HTTPException(
            status_code=500,
            detail="ORCHESTRATOR_HTTP_MAX_RETRIES must be >= 0",
        )
    if retry_backoff_seconds < 0:
        raise HTTPException(
            status_code=500,
            detail="ORCHESTRATOR_HTTP_RETRY_BACKOFF_SECONDS must be >= 0",
        )
    return OrchestratorClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        internal_api_token=internal_api_token,
        role=role,
        principal_id=principal_id,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def _participant_seed(token: str) -> dict[str, Any]:
    normalized = token.strip().lower()
    if normalized.startswith("ai"):
        display_name = "AI(2)" if "2" in normalized else "AI(1)"
        return {
            "kind": "ai",
            "display_name": display_name,
            "role_label": normalized,
            "metadata": {"template_participant": token},
        }
    if normalized.startswith("human"):
        return {
            "kind": "human",
            "display_name": token,
            "role_label": normalized,
            "metadata": {"template_participant": token},
        }
    return {
        "kind": "system",
        "display_name": token,
        "role_label": normalized,
        "metadata": {"template_participant": token},
    }


def _to_template_summary_response(
    row: AutomationTemplateListRecord,
) -> AutomationTemplateSummaryResponse:
    return AutomationTemplateSummaryResponse(
        template_id=row.template_id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        rrule=row.rrule,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _parse_template_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None or not cursor.strip():
        return None, None
    token = cursor.strip()
    prefix = "u:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 'u:'")
    parts = token[len(prefix) :].split("|", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="cursor must be '<updated_at>|<id>'")
    timestamp_raw, template_id_raw = parts[0], parts[1]
    try:
        updated_at = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor timestamp is invalid") from exc
    if updated_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="cursor timestamp must include timezone")
    try:
        template_id = UUID(template_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor template id is invalid") from exc
    return updated_at, template_id


def _build_template_cursor(row: AutomationTemplateListRecord) -> str:
    updated_at_utc = row.updated_at.astimezone(timezone.utc)
    token = updated_at_utc.isoformat().replace("+00:00", "Z")
    return f"u:{token}|{row.template_id}"


def _to_automation_run_response(row: AutomationRunRecord) -> AutomationRunResponse:
    return AutomationRunResponse(
        run_id=row.run_id,
        template_id=row.template_id,
        scheduled_for=row.scheduled_for,
        idempotency_key=row.idempotency_key,
        status=row.status,
        triggered_at=row.triggered_at,
        metadata=row.metadata,
    )


def _parse_run_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    if cursor is None or not cursor.strip():
        return None, None
    token = cursor.strip()
    prefix = "r:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 'r:'")
    parts = token[len(prefix) :].split("|", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="cursor must be '<scheduled_for>|<run_id>'")
    scheduled_raw, run_id_raw = parts[0], parts[1]
    try:
        scheduled_for = datetime.fromisoformat(scheduled_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="cursor scheduled_for is invalid"
        ) from exc
    if scheduled_for.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail="cursor scheduled_for must include timezone",
        )
    try:
        run_id = int(run_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor run_id must be an integer") from exc
    if run_id < 1:
        raise HTTPException(status_code=400, detail="cursor run_id must be >= 1")
    return scheduled_for, run_id


def _build_run_cursor(row: AutomationRunRecord) -> str:
    scheduled_utc = row.scheduled_for.astimezone(timezone.utc)
    token = scheduled_utc.isoformat().replace("+00:00", "Z")
    return f"r:{token}|{row.run_id}"


def _authorize(
    request: Request,
    *,
    allowed_roles: set[str] | None = None,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    auth = get_auth_context(request)
    if allowed_roles is not None:
        enforce_role(auth, allowed_roles=allowed_roles)
    enforce_scope(auth, tenant_id=tenant_id, workspace_id=workspace_id)


@app.post(
    "/internal/automation/templates",
    response_model=CreateAutomationTemplateResponse,
    status_code=201,
)
def create_automation_template(
    request: CreateAutomationTemplateRequest,
    http_request: Request,
) -> CreateAutomationTemplateResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "system"},
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
    )
    connection = _connect()
    repository = _build_repository(connection)
    try:
        created: CreateAutomationTemplateResult = repository.create_template(
            CreateAutomationTemplateInput(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                name=request.name,
                conversation_objective=request.conversation_objective,
                rrule=request.rrule,
                participants=request.participants,
                enabled=request.enabled,
                metadata=request.metadata,
            )
        )
    finally:
        connection.close()

    return CreateAutomationTemplateResponse(
        template_id=created.template_id,
        created_at=created.created_at,
    )


@app.get(
    "/internal/automation/templates",
    response_model=list[AutomationTemplateSummaryResponse],
)
def list_automation_templates(
    http_request: Request,
    tenant_id: UUID = Query(...),
    workspace_id: UUID = Query(...),
    include_disabled: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AutomationTemplateSummaryResponse]:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    connection = _connect()
    repository = _build_repository(connection)
    try:
        rows: list[AutomationTemplateListRecord] = repository.list_templates(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_disabled=include_disabled,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_template_summary_response(row) for row in rows]


@app.get(
    "/internal/automation/templates/page",
    response_model=AutomationTemplatePageResponse,
)
def list_automation_templates_page(
    http_request: Request,
    tenant_id: UUID = Query(...),
    workspace_id: UUID = Query(...),
    include_disabled: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> AutomationTemplatePageResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    before_updated_at, before_template_id = _parse_template_cursor(cursor)
    connection = _connect()
    repository = _build_repository(connection)
    try:
        rows: list[AutomationTemplateListRecord] = repository.list_templates(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_disabled=include_disabled,
            limit=limit + 1,
            before_updated_at=before_updated_at,
            before_template_id=before_template_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _build_template_cursor(page_rows[-1]) if has_more and page_rows else None
    return AutomationTemplatePageResponse(
        items=[_to_template_summary_response(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get(
    "/internal/automation/templates/{template_id}",
    response_model=AutomationTemplateDetailResponse,
)
def get_automation_template(
    template_id: UUID,
    http_request: Request,
) -> AutomationTemplateDetailResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        row: AutomationTemplateDetailRecord = repository.get_template_detail(template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
    )

    return AutomationTemplateDetailResponse(
        template_id=row.template_id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        conversation_objective=row.conversation_objective,
        rrule=row.rrule,
        participants=row.participants,
        enabled=row.enabled,
        metadata=row.metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.patch(
    "/internal/automation/templates/{template_id}/enabled",
    response_model=UpdateTemplateEnabledResponse,
)
def update_automation_template_enabled(
    template_id: UUID,
    request: UpdateTemplateEnabledRequest,
    http_request: Request,
) -> UpdateTemplateEnabledResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "system"})
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template = repository.get_template_detail(template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
        result: SetTemplateEnabledResult = repository.set_template_enabled(
            template_id=template_id,
            enabled=request.enabled,
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return UpdateTemplateEnabledResponse(
        template_id=result.template_id,
        enabled=result.enabled,
        updated_at=result.updated_at,
    )


@app.patch(
    "/internal/automation/templates/{template_id}",
    response_model=AutomationTemplateDetailResponse,
)
def update_automation_template(
    template_id: UUID,
    request: UpdateAutomationTemplateRequest,
    http_request: Request,
) -> AutomationTemplateDetailResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "system"})
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template = repository.get_template_detail(template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
        row: AutomationTemplateDetailRecord = repository.update_template(
            UpdateAutomationTemplateInput(
                template_id=template_id,
                name=request.name,
                conversation_objective=request.conversation_objective,
                rrule=request.rrule,
                participants=request.participants,
                metadata=request.metadata,
            )
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return AutomationTemplateDetailResponse(
        template_id=row.template_id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        name=row.name,
        conversation_objective=row.conversation_objective,
        rrule=row.rrule,
        participants=row.participants,
        enabled=row.enabled,
        metadata=row.metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.post(
    "/internal/scheduler/runs/trigger",
    response_model=TriggerAutomationRunResponse,
)
def trigger_automation_run(
    request: TriggerAutomationRunRequest,
    http_request: Request,
) -> TriggerAutomationRunResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "system"})
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template = repository.get_template(request.template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
        result: TriggerAutomationRunResult = repository.trigger_run(
            TriggerAutomationRunInput(
                template_id=request.template_id,
                scheduled_for=request.scheduled_for,
                metadata=request.metadata,
            )
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return TriggerAutomationRunResponse(
        run_id=result.run_id,
        template_id=result.template_id,
        scheduled_for=result.scheduled_for,
        idempotency_key=result.idempotency_key,
        status=result.status,
        triggered_at=result.triggered_at,
    )


@app.post(
    "/internal/scheduler/runs/execute",
    response_model=ExecuteAutomationRunResponse,
)
def execute_automation_run(
    request: ExecuteAutomationRunRequest,
    http_request: Request,
) -> ExecuteAutomationRunResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "system"})
    return _execute_automation_run_internal(request=request, http_request=http_request)


def _execute_automation_run_internal(
    *,
    request: ExecuteAutomationRunRequest,
    http_request: Request | None,
) -> ExecuteAutomationRunResponse:
    client: OrchestratorClient | None = None
    if request.auto_start_conversation:
        client = _build_orchestrator_client()

    connection = _connect()
    repository = _build_repository(connection)
    try:
        template: AutomationTemplateRecord = repository.get_template(request.template_id)
        if http_request is not None:
            _authorize(
                http_request,
                allowed_roles={"admin", "operator", "system"},
                tenant_id=template.tenant_id,
                workspace_id=template.workspace_id,
            )
        run_result: TriggerAutomationRunResult = repository.trigger_run(
            TriggerAutomationRunInput(
                template_id=request.template_id,
                scheduled_for=request.scheduled_for,
                metadata=request.metadata,
            )
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    if not request.auto_start_conversation:
        return ExecuteAutomationRunResponse(
            run_id=run_result.run_id,
            template_id=run_result.template_id,
            scheduled_for=run_result.scheduled_for,
            idempotency_key=run_result.idempotency_key,
            status=run_result.status,
            triggered_at=run_result.triggered_at,
            conversation_started=False,
        )

    if client is None:
        return ExecuteAutomationRunResponse(
            run_id=run_result.run_id,
            template_id=run_result.template_id,
            scheduled_for=run_result.scheduled_for,
            idempotency_key=run_result.idempotency_key,
            status=run_result.status,
            triggered_at=run_result.triggered_at,
            conversation_started=False,
        )

    participants = [_participant_seed(participant) for participant in template.participants]
    request_id = None
    if http_request is not None:
        request_id = getattr(http_request.state, "request_id", None)
    if request_id is None:
        request_id = f"scheduler-run-{run_result.run_id}"
    try:
        start_result: StartAutomationConversationClientResult = (
            client.start_automation_conversation(
                StartAutomationConversationClientInput(
                    tenant_id=template.tenant_id,
                    workspace_id=template.workspace_id,
                    title=template.name,
                    objective=template.conversation_objective,
                    automation_template_id=template.template_id,
                    automation_run_id=str(run_result.run_id),
                    scheduled_for=run_result.scheduled_for,
                    participants=participants,
                    metadata={
                        "scheduler_status": run_result.status,
                        **request.metadata,
                    },
                    request_id=request_id,
                )
            )
        )
    except OrchestratorCallError as exc:
        failure_connection = _connect()
        failure_repository = _build_repository(failure_connection)
        try:
            failure_repository.mark_run_failed(
                run_id=run_result.run_id,
                error_message=str(exc),
                metadata={
                    "orchestrator_error": str(exc),
                    "scheduler_execute": True,
                    **request.metadata,
                },
            )
        except AutomationRunNotFoundError:
            pass
        finally:
            failure_connection.close()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ExecuteAutomationRunResponse(
        run_id=run_result.run_id,
        template_id=run_result.template_id,
        scheduled_for=run_result.scheduled_for,
        idempotency_key=run_result.idempotency_key,
        status=run_result.status,
        triggered_at=run_result.triggered_at,
        conversation_started=True,
        conversation_id=start_result.conversation_id,
        conversation_created=start_result.created,
    )


@app.post(
    "/internal/scheduler/runs/execute-batch",
    response_model=BatchExecuteAutomationRunsResponse,
)
def execute_automation_runs_batch(
    request: BatchExecuteAutomationRunsRequest,
    http_request: Request,
) -> BatchExecuteAutomationRunsResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "system"})
    succeeded = 0
    items: list[BatchExecuteAutomationRunItemResponse] = []

    for template_id in request.template_ids:
        try:
            result = _execute_automation_run_internal(
                request=ExecuteAutomationRunRequest(
                    template_id=template_id,
                    scheduled_for=request.scheduled_for,
                    metadata=request.metadata,
                    auto_start_conversation=request.auto_start_conversation,
                ),
                http_request=http_request,
            )
            succeeded += 1
            items.append(
                BatchExecuteAutomationRunItemResponse(
                    template_id=template_id,
                    success=True,
                    run_id=result.run_id,
                    status=result.status,
                    conversation_started=result.conversation_started,
                    conversation_id=result.conversation_id,
                    conversation_created=result.conversation_created,
                )
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                error_code = "template_not_found"
            elif exc.status_code == 403:
                error_code = "forbidden_scope"
            elif exc.status_code == 502:
                error_code = "orchestrator_error"
            elif exc.status_code == 500 and "orchestrator" in str(exc.detail).lower():
                error_code = "orchestrator_config_error"
            else:
                error_code = "execute_error"
            items.append(
                BatchExecuteAutomationRunItemResponse(
                    template_id=template_id,
                    success=False,
                    run_id=None,
                    status=None,
                    conversation_started=False,
                    error_code=error_code,
                    error_message=str(exc.detail),
                )
            )

    return BatchExecuteAutomationRunsResponse(
        processed=len(request.template_ids),
        succeeded=succeeded,
        failed=len(request.template_ids) - succeeded,
        items=items,
    )


@app.post("/internal/scheduler/runs/preview", response_model=PreviewAutomationRunResponse)
def preview_automation_run(
    request: PreviewAutomationRunRequest,
    http_request: Request,
) -> PreviewAutomationRunResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template: AutomationTemplateRecord = repository.get_template(request.template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=template.tenant_id,
        workspace_id=template.workspace_id,
    )

    normalized = normalize_scheduled_for(request.scheduled_for)
    idempotency_key = build_idempotency_key(request.template_id, normalized)
    participants = [_participant_seed(participant) for participant in template.participants]
    start_payload = {
        "tenant_id": str(template.tenant_id),
        "workspace_id": str(template.workspace_id),
        "title": template.name,
        "objective": template.conversation_objective,
        "automation_template_id": str(template.template_id),
        "automation_run_id": "{run_id}",
        "scheduled_for": normalized.isoformat(),
        "participants": participants,
        "metadata": {"preview": True, **request.metadata},
    }
    return PreviewAutomationRunResponse(
        template_id=request.template_id,
        scheduled_for=normalized,
        idempotency_key=idempotency_key,
        participants=participants,
        start_payload=start_payload,
    )


@app.get(
    "/internal/automation/templates/{template_id}/runs",
    response_model=list[AutomationRunResponse],
)
def list_template_runs(
    template_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AutomationRunResponse]:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template = repository.get_template_detail(template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "viewer", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
        rows: list[AutomationRunRecord] = repository.list_runs(
            template_id=template_id,
            limit=limit,
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_automation_run_response(row) for row in rows]


@app.get(
    "/internal/automation/templates/{template_id}/runs/page",
    response_model=AutomationRunPageResponse,
)
def list_template_runs_page(
    template_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> AutomationRunPageResponse:
    before_scheduled_for, before_run_id = _parse_run_cursor(cursor)
    connection = _connect()
    repository = _build_repository(connection)
    try:
        template = repository.get_template_detail(template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "viewer", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
        rows: list[AutomationRunRecord] = repository.list_runs(
            template_id=template_id,
            limit=limit + 1,
            before_scheduled_for=before_scheduled_for,
            before_run_id=before_run_id,
        )
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _build_run_cursor(page_rows[-1]) if has_more and page_rows else None
    return AutomationRunPageResponse(
        items=[_to_automation_run_response(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get(
    "/internal/scheduler/runs/{run_id}",
    response_model=AutomationRunResponse,
)
def get_automation_run(run_id: int, http_request: Request) -> AutomationRunResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        row: AutomationRunRecord = repository.get_run(run_id=run_id)
        template = repository.get_template_detail(row.template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "viewer", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
    except AutomationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return _to_automation_run_response(row)


@app.post(
    "/internal/scheduler/runs/{run_id}/retry",
    response_model=RetryAutomationRunResponse,
)
def retry_automation_run(
    run_id: int,
    request: RetryAutomationRunRequest,
    http_request: Request,
) -> RetryAutomationRunResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        source_run: AutomationRunRecord = repository.get_run(run_id=run_id)
        template = repository.get_template_detail(source_run.template_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "system"},
            tenant_id=template.tenant_id,
            workspace_id=template.workspace_id,
        )
    except AutomationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    if source_run.status != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"Automation run {run_id} is not retryable (status={source_run.status})",
        )

    exec_result = _execute_automation_run_internal(
        request=ExecuteAutomationRunRequest(
            template_id=source_run.template_id,
            scheduled_for=source_run.scheduled_for,
            metadata={**source_run.metadata, **request.metadata},
            auto_start_conversation=request.auto_start_conversation,
        ),
        http_request=http_request,
    )

    return RetryAutomationRunResponse(
        source_run_id=run_id,
        run_id=exec_result.run_id,
        template_id=exec_result.template_id,
        scheduled_for=exec_result.scheduled_for,
        idempotency_key=exec_result.idempotency_key,
        status=exec_result.status,
        triggered_at=exec_result.triggered_at,
        conversation_started=exec_result.conversation_started,
        conversation_id=exec_result.conversation_id,
        conversation_created=exec_result.conversation_created,
    )
