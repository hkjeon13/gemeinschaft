"""Export service for conversation dataset extraction."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from services.export_service.repository import (
    ConversationScopeRecord,
    ConversationForExportNotFoundError,
    CreateExportJobInput,
    DatasetVersionRecord,
    DatasetVersionNotFoundError,
    ExportArtifactNotFoundError,
    ExportJobNotFoundError,
    ExportJobRecord,
    ExportRepository,
    InvalidExportStorageKeyError,
)
from services.shared.app_factory import build_service_app
from services.shared.auth import enforce_role, enforce_scope, get_auth_context
from services.shared.db import get_db_connection

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


class ExportJobPageResponse(BaseModel):
    items: list[ExportJobResponse]
    next_cursor: str | None
    has_more: bool


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


class DatasetVersionPageResponse(BaseModel):
    items: list[DatasetVersionResponse]
    next_cursor: str | None
    has_more: bool


def _media_type_for_export_format(export_format: str) -> str:
    return (
        "application/x-ndjson"
        if export_format == "jsonl"
        else "text/csv; charset=utf-8"
    )


def _connect() -> Any:
    try:
        return get_db_connection("export_service")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_repository(connection: Any) -> ExportRepository:
    export_root = Path(os.getenv("EXPORT_STORAGE_DIR", ".local/exports"))
    return ExportRepository(connection=connection, export_root=export_root)


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


def _authorize_conversation_scope(
    request: Request,
    *,
    repository: ExportRepository,
    conversation_id: UUID,
    allowed_roles: set[str],
) -> None:
    _authorize(request, allowed_roles=allowed_roles)
    auth = get_auth_context(request)
    if auth.tenant_id is None and auth.workspace_id is None:
        return
    try:
        scope: ConversationScopeRecord = repository.get_conversation_scope(conversation_id)
    except ConversationForExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    enforce_scope(auth, tenant_id=scope.tenant_id, workspace_id=scope.workspace_id)


def _to_export_job_response(record: ExportJobRecord) -> ExportJobResponse:
    return ExportJobResponse(
        job_id=record.job_id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        conversation_id=record.conversation_id,
        export_format=record.export_format,
        status=record.status,
        storage_key=record.storage_key,
        row_count=record.row_count,
        manifest=record.manifest,
        requested_by_user_id=record.requested_by_user_id,
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


def _parse_export_job_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None or not cursor.strip():
        return None, None
    token = cursor.strip()
    prefix = "j:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 'j:'")
    parts = token[len(prefix) :].split("|", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="cursor must be '<created_at>|<job_id>'")
    created_at_raw, job_id_raw = parts[0], parts[1]
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor created_at is invalid") from exc
    if created_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="cursor created_at must include timezone")
    try:
        job_id = UUID(job_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor job_id is invalid") from exc
    return created_at, job_id


def _build_export_job_cursor(record: ExportJobRecord) -> str:
    created_at_utc = record.created_at.astimezone(timezone.utc)
    token = created_at_utc.isoformat().replace("+00:00", "Z")
    return f"j:{token}|{record.job_id}"


def _parse_dataset_version_cursor(cursor: str | None) -> int | None:
    if cursor is None or not cursor.strip():
        return None
    token = cursor.strip()
    prefix = "v:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 'v:'")
    raw_value = token[len(prefix) :]
    try:
        version_no = int(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor version must be an integer") from exc
    if version_no < 1:
        raise HTTPException(status_code=400, detail="cursor version must be >= 1")
    return version_no


def _build_dataset_version_cursor(version_no: int) -> str:
    return f"v:{version_no}"


def _to_dataset_version_response(record: DatasetVersionRecord) -> DatasetVersionResponse:
    return DatasetVersionResponse(
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


@app.post("/internal/exports/jobs", response_model=ExportJobResponse, status_code=201)
def create_export_job(
    request: CreateExportJobRequest,
    http_request: Request,
) -> ExportJobResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "system"},
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
    )
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

    return _to_export_job_response(result)


@app.get("/internal/exports/jobs/{job_id}", response_model=ExportJobResponse)
def get_export_job(job_id: UUID, http_request: Request) -> ExportJobResponse:
    _authorize(http_request, allowed_roles={"admin", "operator", "viewer", "system"})
    connection = _connect()
    repository = _build_repository(connection)
    try:
        result: ExportJobRecord = repository.get_export_job(job_id)
    except ExportJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=result.tenant_id,
        workspace_id=result.workspace_id,
    )

    return _to_export_job_response(result)


@app.get(
    "/internal/conversations/{conversation_id}/exports/jobs/page",
    response_model=ExportJobPageResponse,
)
def list_conversation_export_jobs_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ExportJobPageResponse:
    before_created_at, before_job_id = _parse_export_job_cursor(cursor)
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        records: list[ExportJobRecord] = repository.list_export_jobs(
            conversation_id=conversation_id,
            limit=limit + 1,
            before_created_at=before_created_at,
            before_job_id=before_job_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_export_job_cursor(page_records[-1])
    return ExportJobPageResponse(
        items=[_to_export_job_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get("/internal/exports/jobs/{job_id}/download")
def download_export_job(job_id: UUID, http_request: Request) -> Response:
    _authorize(http_request, allowed_roles={"admin", "operator", "viewer", "system"})
    connection = _connect()
    repository = _build_repository(connection)
    try:
        record = repository.get_export_job(job_id)
        _authorize(
            http_request,
            allowed_roles={"admin", "operator", "viewer", "system"},
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
        )
        _, content = repository.read_export_artifact(job_id)
    except ExportJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExportStorageKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    media_type = _media_type_for_export_format(record.export_format)
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
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DatasetVersionResponse]:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        records: list[DatasetVersionRecord] = repository.list_dataset_versions(
            conversation_id=conversation_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_dataset_version_response(record) for record in records]


@app.get(
    "/internal/conversations/{conversation_id}/exports/versions/page",
    response_model=DatasetVersionPageResponse,
)
def list_conversation_dataset_versions_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> DatasetVersionPageResponse:
    before_version_no = _parse_dataset_version_cursor(cursor)
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        records: list[DatasetVersionRecord] = repository.list_dataset_versions(
            conversation_id=conversation_id,
            limit=limit + 1,
            before_version_no=before_version_no,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_dataset_version_cursor(page_records[-1].version_no)
    return DatasetVersionPageResponse(
        items=[_to_dataset_version_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get(
    "/internal/conversations/{conversation_id}/exports/versions/latest",
    response_model=DatasetVersionResponse,
)
def get_latest_dataset_version(
    conversation_id: UUID, http_request: Request
) -> DatasetVersionResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        record = repository.get_latest_dataset_version(conversation_id=conversation_id)
    except DatasetVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return DatasetVersionResponse(
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


@app.get(
    "/internal/conversations/{conversation_id}/exports/versions/{version_no}",
    response_model=DatasetVersionResponse,
)
def get_dataset_version(
    conversation_id: UUID, version_no: int, http_request: Request
) -> DatasetVersionResponse:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        record = repository.get_dataset_version(
            conversation_id=conversation_id,
            version_no=version_no,
        )
    except DatasetVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return DatasetVersionResponse(
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


@app.get("/internal/conversations/{conversation_id}/exports/versions/latest/download")
def download_latest_dataset_version(
    conversation_id: UUID, http_request: Request
) -> Response:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        record, content = repository.read_dataset_version_artifact(
            conversation_id=conversation_id,
            version_no=None,
        )
    except DatasetVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExportStorageKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    filename = f"conversation-{record.conversation_id}-v{record.version_no}.{record.export_format}"
    return Response(
        content=content,
        media_type=_media_type_for_export_format(record.export_format),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/internal/conversations/{conversation_id}/exports/versions/{version_no}/download")
def download_dataset_version(
    conversation_id: UUID, version_no: int, http_request: Request
) -> Response:
    connection = _connect()
    repository = _build_repository(connection)
    try:
        _authorize_conversation_scope(
            http_request,
            repository=repository,
            conversation_id=conversation_id,
            allowed_roles={"admin", "operator", "viewer", "system"},
        )
        record, content = repository.read_dataset_version_artifact(
            conversation_id=conversation_id,
            version_no=version_no,
        )
    except DatasetVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExportStorageKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    filename = f"conversation-{record.conversation_id}-v{record.version_no}.{record.export_format}"
    return Response(
        content=content,
        media_type=_media_type_for_export_format(record.export_format),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
