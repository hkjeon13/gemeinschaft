"""API tests for source listing page endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module
from services.data_ingestion.source_repository import SourceListRecord


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SourceRepositoryStub:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._created_at_1 = datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
        self._created_at_2 = datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc)
        self._id_1 = uuid4()
        self._id_2 = uuid4()
        self._tenant_id = uuid4()
        self._workspace_id = uuid4()

    def list_sources(
        self,
        *,
        tenant_id: Any,
        workspace_id: Any,
        source_type: str | None = None,
        limit: int = 20,
        before_created_at: datetime | None = None,
        before_source_id: Any | None = None,
    ) -> list[SourceListRecord]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "limit": limit,
                "before_created_at": before_created_at,
                "before_source_id": before_source_id,
            }
        )
        rows = [
            SourceListRecord(
                source_id=self._id_1,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_type="upload",
                original_filename="a.txt",
                content_type="text/plain",
                byte_size=10,
                checksum_sha256="sha-a",
                storage_provider="local_fs",
                storage_key="k/a",
                metadata={"origin": "test"},
                created_at=self._created_at_1,
            ),
            SourceListRecord(
                source_id=self._id_2,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_type="integration",
                original_filename="b.csv",
                content_type="text/csv",
                byte_size=20,
                checksum_sha256="sha-b",
                storage_provider="local_fs",
                storage_key="k/b",
                metadata={"origin": "test"},
                created_at=self._created_at_2,
            ),
        ]
        if source_type is not None:
            rows = [row for row in rows if row.source_type == source_type]
        if before_created_at is not None and before_source_id is not None:
            rows = [
                row
                for row in rows
                if row.created_at < before_created_at
                or (row.created_at == before_created_at and row.source_id < before_source_id)
            ]
        return rows[:limit]


class InvalidSourceTypeRepositoryStub:
    def list_sources(
        self,
        *,
        tenant_id: Any,
        workspace_id: Any,
        source_type: str | None = None,
        limit: int = 20,
        before_created_at: datetime | None = None,
        before_source_id: Any | None = None,
    ) -> list[SourceListRecord]:
        del tenant_id, workspace_id, source_type, limit, before_created_at, before_source_id
        raise ValueError("source_type must be one of: upload, preloaded, integration")


def test_list_sources_page_success(monkeypatch: Any) -> None:
    repo = SourceRepositoryStub()
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(data_ingestion_app_module, "SourceRepository", lambda connection: repo)
    client = TestClient(data_ingestion_app_module.app)

    first = client.get(
        "/internal/sources/page",
        params={
            "tenant_id": str(repo._tenant_id),
            "workspace_id": str(repo._workspace_id),
            "limit": 1,
        },
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["source_id"] == str(repo._id_1)
    assert first_payload["next_cursor"] == f"s:2026-02-28T09:00:00Z|{repo._id_1}"
    assert first_payload["has_more"] is True
    assert repo.calls[0]["limit"] == 2

    second = client.get(
        "/internal/sources/page",
        params={
            "tenant_id": str(repo._tenant_id),
            "workspace_id": str(repo._workspace_id),
            "limit": 1,
            "cursor": first_payload["next_cursor"],
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["source_id"] == str(repo._id_2)
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False
    assert repo.calls[1]["before_created_at"] == repo._created_at_1
    assert repo.calls[1]["before_source_id"] == repo._id_1


def test_list_sources_page_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "SourceRepository",
        lambda connection: SourceRepositoryStub(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.get(
        "/internal/sources/page",
        params={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "cursor": "bad",
        },
    )

    assert response.status_code == 400


def test_list_sources_page_invalid_source_type(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "SourceRepository",
        lambda connection: InvalidSourceTypeRepositoryStub(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.get(
        "/internal/sources/page",
        params={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "source_type": "bad",
        },
    )

    assert response.status_code == 400
