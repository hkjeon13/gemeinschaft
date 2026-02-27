"""Data ingestion service app."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import File, Form, HTTPException, UploadFile
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
    SourceRepository,
)
from services.shared.app_factory import build_service_app

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
        raise HTTPException(status_code=500, detail="psycopg is not installed") from exc
    return psycopg.connect(database_url)


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


@app.post("/internal/sources/upload", response_model=UploadSourceResponse, status_code=201)
async def upload_source(
    tenant_id: UUID = Form(...),
    workspace_id: UUID = Form(...),
    source_type: str = Form("upload"),
    metadata: str | None = Form(None),
    file: UploadFile = File(...),
) -> UploadSourceResponse:
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


@app.post("/internal/sources/{source_id}/process", response_model=ProcessSourceResponse)
def process_source(source_id: UUID) -> ProcessSourceResponse:
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
def embed_source(source_id: UUID) -> EmbedSourceResponse:
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
