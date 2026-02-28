"""Data ingestion auth role/scope guard tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module
from services.data_ingestion.ingestion_worker import ProcessSourceResult
from services.data_ingestion.source_repository import SourceListRecord


class ScopeConnection:
    def __init__(self, tenant_id: Any, workspace_id: Any):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.closed = False
        self._last_fetchone: Any = None

    def cursor(self) -> "ScopeConnection":
        return self

    def __enter__(self) -> "ScopeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "from source_document where id = %s" in normalized_sql:
            self._last_fetchone = (
                params[0],
                self.tenant_id,
                self.workspace_id,
                "upload",
                "doc.txt",
                "text/plain",
                1,
                "sha",
                "local_fs",
                "key",
                {},
            )
            return
        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def close(self) -> None:
        self.closed = True


class ProcessWorkerStub:
    def process_source(self, source_id: Any) -> ProcessSourceResult:
        return ProcessSourceResult(source_id=source_id, status="processed", chunk_count=1)


class SourceListRepositoryStub:
    calls = 0

    def list_sources(
        self,
        *,
        tenant_id: Any,
        workspace_id: Any,
        source_type: str | None = None,
        limit: int = 20,
        before_created_at: Any | None = None,
        before_source_id: Any | None = None,
    ) -> list[SourceListRecord]:
        del source_type, limit, before_created_at, before_source_id
        type(self).calls += 1
        return [
            SourceListRecord(
                source_id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_type="upload",
                original_filename="doc.txt",
                content_type="text/plain",
                byte_size=1,
                checksum_sha256="sha",
                storage_provider="local_fs",
                storage_key="key",
                metadata={},
                created_at=datetime(2026, 2, 28, 9, 30, tzinfo=timezone.utc),
            )
        ]


def test_upload_source_rejects_viewer_role(monkeypatch: Any) -> None:
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(
        "/internal/sources/upload",
        headers={"x-internal-role": "viewer"},
        files={"file": ("doc.txt", b"hello", "text/plain")},
        data={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "source_type": "upload",
        },
    )

    assert response.status_code == 403


def test_process_source_rejects_scope_mismatch(monkeypatch: Any) -> None:
    resource_tenant = uuid4()
    resource_workspace = uuid4()
    auth_tenant = uuid4()
    auth_workspace = uuid4()
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_connect",
        lambda: ScopeConnection(resource_tenant, resource_workspace),
    )
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: object())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_ingestion_worker",
        lambda connection, storage: ProcessWorkerStub(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(
        f"/internal/sources/{uuid4()}/process",
        headers={
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(auth_tenant),
            "x-auth-workspace-id": str(auth_workspace),
        },
    )

    assert response.status_code == 403


def test_process_source_allows_matching_scope(monkeypatch: Any) -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_connect",
        lambda: ScopeConnection(tenant_id, workspace_id),
    )
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: object())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_ingestion_worker",
        lambda connection, storage: ProcessWorkerStub(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(
        f"/internal/sources/{uuid4()}/process",
        headers={
            "x-internal-role": "operator",
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200


def test_list_sources_page_rejects_scope_mismatch_without_query(monkeypatch: Any) -> None:
    SourceListRepositoryStub.calls = 0
    client = TestClient(data_ingestion_app_module.app)

    response = client.get(
        "/internal/sources/page",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
        params={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "limit": 10,
        },
    )

    assert response.status_code == 403
    assert SourceListRepositoryStub.calls == 0


def test_list_sources_page_allows_matching_scope(monkeypatch: Any) -> None:
    SourceListRepositoryStub.calls = 0
    repo = SourceListRepositoryStub()
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_connect",
        lambda: ScopeConnection(uuid4(), uuid4()),
    )
    monkeypatch.setattr(data_ingestion_app_module, "SourceRepository", lambda connection: repo)
    client = TestClient(data_ingestion_app_module.app)
    tenant_id = uuid4()
    workspace_id = uuid4()

    response = client.get(
        "/internal/sources/page",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
        params={
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert SourceListRepositoryStub.calls == 1
