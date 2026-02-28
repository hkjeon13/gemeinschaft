"""Scope resolver for source document ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.processing_repository import IngestionProcessingRepository


@dataclass(frozen=True)
class SourceScope:
    tenant_id: UUID
    workspace_id: UUID


class SourceScopeService:
    def __init__(self, connection: Any):
        self._repository = IngestionProcessingRepository(connection)

    def get_scope(self, source_id: UUID) -> SourceScope:
        row = self._repository.get_source_document(source_id)
        if row is None:
            raise SourceNotFoundError(f"Source document {source_id} not found")
        return SourceScope(
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
        )
