"""Scheduler service app."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.scheduler.repository import (
    CreateAutomationTemplateInput,
    CreateAutomationTemplateResult,
    SchedulerRepository,
    TemplateNotFoundError,
    TriggerAutomationRunInput,
    TriggerAutomationRunResult,
)
from services.shared.app_factory import build_service_app

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


def _connect() -> Any:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        raise HTTPException(status_code=500, detail="psycopg is not installed") from exc
    return psycopg.connect(database_url)


def _build_repository(connection: Any) -> SchedulerRepository:
    return SchedulerRepository(connection)


@app.post(
    "/internal/automation/templates",
    response_model=CreateAutomationTemplateResponse,
    status_code=201,
)
def create_automation_template(
    request: CreateAutomationTemplateRequest,
) -> CreateAutomationTemplateResponse:
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


@app.post(
    "/internal/scheduler/runs/trigger",
    response_model=TriggerAutomationRunResponse,
)
def trigger_automation_run(request: TriggerAutomationRunRequest) -> TriggerAutomationRunResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
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
