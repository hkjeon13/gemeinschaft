"""DB repository for source metadata persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CreateSourceInput:
    id: UUID
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


@dataclass(frozen=True)
class CreateSourceResult:
    id: UUID
    created_at: datetime


@dataclass(frozen=True)
class SourceListRecord:
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


class SourceRepository:
    def __init__(self, connection: Any):
        self._connection = connection

    def create_source(self, payload: CreateSourceInput) -> CreateSourceResult:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO source_document (
                        id,
                        tenant_id,
                        workspace_id,
                        source_type,
                        original_filename,
                        content_type,
                        byte_size,
                        checksum_sha256,
                        storage_provider,
                        storage_key,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id, created_at
                    """,
                    (
                        str(payload.id),
                        str(payload.tenant_id),
                        str(payload.workspace_id),
                        payload.source_type,
                        payload.original_filename,
                        payload.content_type,
                        payload.byte_size,
                        payload.checksum_sha256,
                        payload.storage_provider,
                        payload.storage_key,
                        json.dumps(payload.metadata),
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("Source insert did not return a row")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return CreateSourceResult(id=row[0], created_at=row[1])

    def list_sources(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        source_type: str | None = None,
        limit: int = 20,
        before_created_at: datetime | None = None,
        before_source_id: UUID | None = None,
    ) -> list[SourceListRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if source_type is not None and source_type not in {"upload", "preloaded", "integration"}:
            raise ValueError("source_type must be one of: upload, preloaded, integration")
        if (before_created_at is None) != (before_source_id is None):
            raise ValueError("before_created_at and before_source_id must be provided together")

        with self._connection.cursor() as cursor:
            if source_type is None and before_created_at is None:
                cursor.execute(
                    """
                    SELECT
                        id,
                        tenant_id,
                        workspace_id,
                        source_type,
                        original_filename,
                        content_type,
                        byte_size,
                        checksum_sha256,
                        storage_provider,
                        storage_key,
                        metadata,
                        created_at
                    FROM source_document
                    WHERE tenant_id = %s AND workspace_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), str(workspace_id), limit),
                )
            elif source_type is not None and before_created_at is None:
                cursor.execute(
                    """
                    SELECT
                        id,
                        tenant_id,
                        workspace_id,
                        source_type,
                        original_filename,
                        content_type,
                        byte_size,
                        checksum_sha256,
                        storage_provider,
                        storage_key,
                        metadata,
                        created_at
                    FROM source_document
                    WHERE tenant_id = %s AND workspace_id = %s AND source_type = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (str(tenant_id), str(workspace_id), source_type, limit),
                )
            elif source_type is None:
                cursor.execute(
                    """
                    SELECT
                        id,
                        tenant_id,
                        workspace_id,
                        source_type,
                        original_filename,
                        content_type,
                        byte_size,
                        checksum_sha256,
                        storage_provider,
                        storage_key,
                        metadata,
                        created_at
                    FROM source_document
                    WHERE
                        tenant_id = %s
                        AND workspace_id = %s
                        AND (
                            created_at < %s
                            OR (created_at = %s AND id < %s)
                        )
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (
                        str(tenant_id),
                        str(workspace_id),
                        before_created_at,
                        before_created_at,
                        str(before_source_id),
                        limit,
                    ),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                        id,
                        tenant_id,
                        workspace_id,
                        source_type,
                        original_filename,
                        content_type,
                        byte_size,
                        checksum_sha256,
                        storage_provider,
                        storage_key,
                        metadata,
                        created_at
                    FROM source_document
                    WHERE
                        tenant_id = %s
                        AND workspace_id = %s
                        AND source_type = %s
                        AND (
                            created_at < %s
                            OR (created_at = %s AND id < %s)
                        )
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (
                        str(tenant_id),
                        str(workspace_id),
                        source_type,
                        before_created_at,
                        before_created_at,
                        str(before_source_id),
                        limit,
                    ),
                )
            rows = cursor.fetchall()

        return [
            SourceListRecord(
                source_id=row[0],
                tenant_id=row[1],
                workspace_id=row[2],
                source_type=row[3],
                original_filename=row[4],
                content_type=row[5],
                byte_size=int(row[6]),
                checksum_sha256=row[7],
                storage_provider=row[8],
                storage_key=row[9],
                metadata=row[10],
                created_at=row[11],
            )
            for row in rows
        ]
