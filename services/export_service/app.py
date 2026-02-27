"""Export service for conversation dataset extraction."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from services.export_service.repository import (
    ConversationForExportNotFoundError,
    CreateExportJobInput,
    DatasetVersionRecord,
    ExportArtifactNotFoundError,
    ExportJobNotFoundError,
    ExportJobRecord,
    ExportRepository,
    InvalidExportStorageKeyError,
)
from services.shared.app_factory import build_service_app

app = build_service_app("export_service")


class CreateExportJobRequest(BaseModel):
    tenant_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    export_format: str = Field(default="jsonl", min_length=1)
    requested_by_user_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ExportJobResponse(BaseModel):
    job_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    export_format: str
    status: str
    storage_key: str
    row_count: int
    manifest: dict[str, Any]
    requested_by_user_id: UUID | None
    created_at: datetime
    completed_at: datetime | None


class DatasetVersionResponse(BaseModel):
    dataset_version_id: UUID
    conversation_id: UUID
    version_no: int
    export_job_id: UUID
    export_format: str
    storage_key: str
    row_count: int
    manifest: dict[str, Any]
    created_at: datetime


def _connect() -> Any:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(
            status_code=500,
            detail="DATABASE_URL is not configured",
        )
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        raise HTTPException(
            status_code=500,
            detail="psycopg is not installed",
        ) from exc
    return psycopg.connect(database_url)


def _build_repository(connection: Any) -> ExportRepository:
    export_root = Path(os.getenv("EXPORT_STORAGE_DIR", ".local/exports"))
    return ExportRepository(connection=connection, export_root=export_root)


@app.post("/internal/exports/jobs", response_model=ExportJobResponse, status_code=201)
def create_export_job(request: CreateExportJobRequest) -> ExportJobResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        result: ExportJobRecord = repository.create_export_job(
            CreateExportJobInput(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                conversation_id=request.conversation_id,
                export_format=request.export_format,
                requested_by_user_id=request.requested_by_user_id,
            )
        )
    except ConversationForExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return ExportJobResponse(
        job_id=result.job_id,
        tenant_id=result.tenant_id,
        workspace_id=result.workspace_id,
        conversation_id=result.conversation_id,
        export_format=result.export_format,
        status=result.status,
        storage_key=result.storage_key,
        row_count=result.row_count,
        manifest=result.manifest,
        requested_by_user_id=result.requested_by_user_id,
        created_at=result.created_at,
        completed_at=result.completed_at,
    )


@app.get("/internal/exports/jobs/{job_id}", response_model=ExportJobResponse)
def get_export_job(job_id: UUID) -> ExportJobResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        result: ExportJobRecord = repository.get_export_job(job_id)
    except ExportJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ExportJobResponse(
        job_id=result.job_id,
        tenant_id=result.tenant_id,
        workspace_id=result.workspace_id,
        conversation_id=result.conversation_id,
        export_format=result.export_format,
        status=result.status,
        storage_key=result.storage_key,
        row_count=result.row_count,
        manifest=result.manifest,
        requested_by_user_id=result.requested_by_user_id,
        created_at=result.created_at,
        completed_at=result.completed_at,
    )


@app.get("/internal/exports/jobs/{job_id}/download")
def download_export_job(job_id: UUID) -> Response:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        record, content = repository.read_export_artifact(job_id)
    except ExportJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExportStorageKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    media_type = (
        "application/x-ndjson"
        if record.export_format == "jsonl"
        else "text/csv; charset=utf-8"
    )
    filename = f"conversation-{record.conversation_id}.{record.export_format}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get(
    "/internal/conversations/{conversation_id}/exports/versions",
    response_model=list[DatasetVersionResponse],
)
def list_conversation_dataset_versions(
    conversation_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DatasetVersionResponse]:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        records: list[DatasetVersionRecord] = repository.list_dataset_versions(
            conversation_id=conversation_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [
        DatasetVersionResponse(
            dataset_version_id=record.dataset_version_id,
            conversation_id=record.conversation_id,
            version_no=record.version_no,
            export_job_id=record.export_job_id,
            export_format=record.export_format,
            storage_key=record.storage_key,
            row_count=record.row_count,
            manifest=record.manifest,
            created_at=record.created_at,
        )
        for record in records
    ]
