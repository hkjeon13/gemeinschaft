"""Data ingestion service app."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from services.data_ingestion.embedding_worker import EmbedSourceResult, EmbeddingWorker
from services.data_ingestion.ingestion_worker import (
    IngestionWorker,
    ProcessSourceResult,
    SourceNotFoundError,
)
from services.data_ingestion.object_storage import LocalObjectStorage, ObjectStorage
from services.data_ingestion.processing_repository import IngestionProcessingRepository
from services.data_ingestion.source_repository import (
    CreateSourceInput,
    CreateSourceResult,
    SourceListRecord,
    SourceRepository,
)
from services.data_ingestion.source_scope_service import SourceScopeService
from services.data_ingestion.topic_worker import TopicSourceResult, TopicWorker
from services.shared.app_factory import build_service_app
from services.shared.auth import enforce_role, enforce_scope, get_auth_context
from services.shared.db import get_db_connection

app = build_service_app("data_ingestion")

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class UploadSourceResponse(BaseModel):
    source_id: UUID
    storage_key: str
    checksum_sha256: str
    byte_size: int
    created_at: datetime


class ProcessSourceResponse(BaseModel):
    source_id: UUID
    status: str
    chunk_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class EmbedSourceResponse(BaseModel):
    source_id: UUID
    status: str
    chunk_count: int
    embedding_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class ClusterTopicsResponse(BaseModel):
    source_id: UUID
    status: str
    topic_count: int
    link_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class SourceSummaryResponse(BaseModel):
    source_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    source_type: str
    original_filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str
    storage_provider: str
    storage_key: str
    metadata: dict[str, Any]
    created_at: datetime


class SourcePageResponse(BaseModel):
    items: list[SourceSummaryResponse]
    next_cursor: str | None
    has_more: bool


def _connect() -> Any:
    try:
        return get_db_connection("data_ingestion")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_storage() -> ObjectStorage:
    provider = os.getenv("OBJECT_STORAGE_PROVIDER", "local_fs")
    if provider != "local_fs":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported OBJECT_STORAGE_PROVIDER={provider!r}",
        )
    root = Path(os.getenv("OBJECT_STORAGE_ROOT", ".local/object_storage"))
    return LocalObjectStorage(root=root)


def _build_ingestion_worker(
    *, connection: Any, storage: ObjectStorage
) -> IngestionWorker:
    max_chunk_chars = int(os.getenv("CHUNK_MAX_CHARS", "1200"))
    overlap_chars = int(os.getenv("CHUNK_OVERLAP_CHARS", "120"))
    repository = IngestionProcessingRepository(connection)
    return IngestionWorker(
        repository=repository,
        storage=storage,
        max_chunk_chars=max_chunk_chars,
        overlap_chars=overlap_chars,
    )


def _build_embedding_worker(*, connection: Any) -> EmbeddingWorker:
    embedding_model = os.getenv("EMBEDDING_MODEL", "hash-v1")
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "128"))
    if embedding_dim != 128:
        raise HTTPException(
            status_code=500,
            detail="EMBEDDING_DIM must be 128 for current source_chunk_embedding schema",
        )
    repository = IngestionProcessingRepository(connection)
    return EmbeddingWorker(
        repository=repository,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
    )


def _build_topic_worker(*, connection: Any) -> TopicWorker:
    similarity_threshold = float(os.getenv("TOPIC_SIMILARITY_THRESHOLD", "0.82"))
    if not 0.0 <= similarity_threshold <= 1.0:
        raise HTTPException(
            status_code=500,
            detail="TOPIC_SIMILARITY_THRESHOLD must be between 0.0 and 1.0",
        )

    embedding_model = os.getenv("EMBEDDING_MODEL", "hash-v1")
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "128"))
    if embedding_dim != 128:
        raise HTTPException(
            status_code=500,
            detail="EMBEDDING_DIM must be 128 for current topic/embedding schema",
        )

    repository = IngestionProcessingRepository(connection)
    return TopicWorker(
        repository=repository,
        similarity_threshold=similarity_threshold,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
    )


def _parse_metadata(metadata: str | None) -> dict[str, Any]:
    if metadata is None or not metadata.strip():
        return {}
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="metadata must be valid JSON object"
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")
    return parsed


def _sanitize_filename(filename: str | None) -> str:
    candidate = Path(filename or "upload.bin").name
    candidate = SAFE_FILENAME_RE.sub("_", candidate)
    return candidate or "upload.bin"


def _build_storage_key(
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    source_id: UUID,
    filename: str,
) -> str:
    return f"{tenant_id}/{workspace_id}/{source_id}/{filename}"


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


def _authorize_source(
    request: Request,
    *,
    source_id: UUID,
    allowed_roles: set[str],
) -> None:
    _authorize(request, allowed_roles=allowed_roles)
    auth = get_auth_context(request)
    if auth.tenant_id is None and auth.workspace_id is None:
        return

    connection = _connect()
    scope_service = SourceScopeService(connection)
    try:
        scope = scope_service.get_scope(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    enforce_scope(auth, tenant_id=scope.tenant_id, workspace_id=scope.workspace_id)


def _to_source_summary_response(record: SourceListRecord) -> SourceSummaryResponse:
    return SourceSummaryResponse(
        source_id=record.source_id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        source_type=record.source_type,
        original_filename=record.original_filename,
        content_type=record.content_type,
        byte_size=record.byte_size,
        checksum_sha256=record.checksum_sha256,
        storage_provider=record.storage_provider,
        storage_key=record.storage_key,
        metadata=record.metadata,
        created_at=record.created_at,
    )


def _parse_source_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None or not cursor.strip():
        return None, None
    token = cursor.strip()
    prefix = "s:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 's:'")
    parts = token[len(prefix) :].split("|", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="cursor must be '<created_at>|<source_id>'")
    created_at_raw, source_id_raw = parts[0], parts[1]
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor created_at is invalid") from exc
    if created_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="cursor created_at must include timezone")
    try:
        source_id = UUID(source_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor source_id is invalid") from exc
    return created_at, source_id


def _build_source_cursor(record: SourceListRecord) -> str:
    created_at_utc = record.created_at.astimezone(timezone.utc)
    token = created_at_utc.isoformat().replace("+00:00", "Z")
    return f"s:{token}|{record.source_id}"


@app.post("/internal/sources/upload", response_model=UploadSourceResponse, status_code=201)
async def upload_source(
    http_request: Request,
    tenant_id: UUID = Form(...),
    workspace_id: UUID = Form(...),
    source_type: str = Form("upload"),
    metadata: str | None = Form(None),
    file: UploadFile = File(...),
) -> UploadSourceResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "system"},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if source_type not in {"upload", "preloaded", "integration"}:
        raise HTTPException(status_code=400, detail="invalid source_type")

    data = await file.read()
    checksum_sha256 = hashlib.sha256(data).hexdigest()
    byte_size = len(data)
    filename = _sanitize_filename(file.filename)
    metadata_payload = _parse_metadata(metadata)

    source_id = uuid4()
    storage_key = _build_storage_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_id=source_id,
        filename=filename,
    )

    storage = _build_storage()
    storage.put_object(storage_key, data, content_type=file.content_type)

    connection = _connect()
    repository = SourceRepository(connection)
    try:
        created: CreateSourceResult = repository.create_source(
            CreateSourceInput(
                id=source_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_type=source_type,
                original_filename=filename,
                content_type=file.content_type,
                byte_size=byte_size,
                checksum_sha256=checksum_sha256,
                storage_provider=storage.provider,
                storage_key=storage_key,
                metadata=metadata_payload,
            )
        )
    except Exception:
        try:
            storage.delete_object(storage_key)
        except Exception:
            pass
        raise
    finally:
        connection.close()

    return UploadSourceResponse(
        source_id=created.id,
        storage_key=storage_key,
        checksum_sha256=checksum_sha256,
        byte_size=byte_size,
        created_at=created.created_at,
    )


@app.get("/internal/sources/page", response_model=SourcePageResponse)
def list_sources_page(
    http_request: Request,
    tenant_id: UUID = Query(...),
    workspace_id: UUID = Query(...),
    source_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> SourcePageResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "viewer", "system"},
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    before_created_at, before_source_id = _parse_source_cursor(cursor)
    connection = _connect()
    repository = SourceRepository(connection)
    try:
        records: list[SourceListRecord] = repository.list_sources(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_type=source_type,
            limit=limit + 1,
            before_created_at=before_created_at,
            before_source_id=before_source_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_source_cursor(page_records[-1])
    return SourcePageResponse(
        items=[_to_source_summary_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.post("/internal/sources/{source_id}/process", response_model=ProcessSourceResponse)
def process_source(source_id: UUID, http_request: Request) -> ProcessSourceResponse:
    _authorize_source(
        http_request,
        source_id=source_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    try:
        storage = _build_storage()
        worker = _build_ingestion_worker(connection=connection, storage=storage)
        result: ProcessSourceResult = worker.process_source(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ProcessSourceResponse(
        source_id=result.source_id,
        status=result.status,
        chunk_count=result.chunk_count,
        dlq_id=result.dlq_id,
        error_type=result.error_type,
        error_message=result.error_message,
    )


@app.post("/internal/sources/{source_id}/embed", response_model=EmbedSourceResponse)
def embed_source(source_id: UUID, http_request: Request) -> EmbedSourceResponse:
    _authorize_source(
        http_request,
        source_id=source_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    try:
        worker = _build_embedding_worker(connection=connection)
        result: EmbedSourceResult = worker.embed_source(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return EmbedSourceResponse(
        source_id=result.source_id,
        status=result.status,
        chunk_count=result.chunk_count,
        embedding_count=result.embedding_count,
        dlq_id=result.dlq_id,
        error_type=result.error_type,
        error_message=result.error_message,
    )


@app.post("/internal/sources/{source_id}/topics", response_model=ClusterTopicsResponse)
def cluster_source_topics(source_id: UUID, http_request: Request) -> ClusterTopicsResponse:
    _authorize_source(
        http_request,
        source_id=source_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    try:
        worker = _build_topic_worker(connection=connection)
        result: TopicSourceResult = worker.cluster_source(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ClusterTopicsResponse(
        source_id=result.source_id,
        status=result.status,
        topic_count=result.topic_count,
        link_count=result.link_count,
        dlq_id=result.dlq_id,
        error_type=result.error_type,
        error_message=result.error_message,
    )
