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
