"""Export service role/scope authorization tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.export_service import app as export_app_module
from services.export_service.repository import (
    ConversationScopeRecord,
    DatasetVersionRecord,
    ExportJobRecord,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ExportRepoStub:
    def __init__(self):
        ts = datetime(2026, 2, 28, 6, 20, tzinfo=timezone.utc)
        self.record = ExportJobRecord(
            job_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            export_format="jsonl",
            status="completed",
            storage_key="/tmp/dataset.jsonl",
            row_count=3,
            manifest={"schema_version": "dataset.v1"},
            requested_by_user_id=None,
            created_at=ts,
            completed_at=ts,
        )
        self.dataset_record = DatasetVersionRecord(
            dataset_version_id=uuid4(),
            conversation_id=self.record.conversation_id,
            version_no=1,
            export_job_id=self.record.job_id,
            export_format="jsonl",
            storage_key="/tmp/dataset-v1.jsonl",
            row_count=3,
            manifest={"schema_version": "dataset.v1"},
            created_at=ts,
        )
        self.read_calls = 0
        self.read_dataset_calls = 0
        self.list_export_jobs_calls = 0

    def create_export_job(self, payload: Any) -> ExportJobRecord:
        return self.record

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        return self.record

    def get_conversation_scope(self, conversation_id: Any) -> ConversationScopeRecord:
        del conversation_id
        return ConversationScopeRecord(
            tenant_id=self.record.tenant_id,
            workspace_id=self.record.workspace_id,
        )

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        del job_id
        self.read_calls += 1
        return self.record, b"{}"

    def list_export_jobs(
        self,
        *,
        conversation_id: Any,
        limit: int,
        before_created_at: datetime | None = None,
        before_job_id: Any | None = None,
    ) -> list[ExportJobRecord]:
        del before_created_at, before_job_id
        self.list_export_jobs_calls += 1
        if conversation_id == self.record.conversation_id:
            return [self.record][:limit]
        return []

    def list_dataset_versions(
        self,
        *,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        del limit, before_version_no
        if conversation_id == self.record.conversation_id:
            return [self.dataset_record]
        return []

    def get_latest_dataset_version(self, *, conversation_id: Any) -> DatasetVersionRecord:
        if conversation_id != self.record.conversation_id:
            raise AssertionError("unexpected conversation_id")
        return self.dataset_record

    def get_dataset_version(
        self, *, conversation_id: Any, version_no: int
    ) -> DatasetVersionRecord:
        if conversation_id != self.record.conversation_id or version_no != 1:
            raise AssertionError("unexpected dataset version request")
        return self.dataset_record

    def read_dataset_version_artifact(
        self, *, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        self.read_dataset_calls += 1
        if conversation_id != self.record.conversation_id:
            raise AssertionError("unexpected conversation_id")
        if version_no not in {None, 1}:
            raise AssertionError("unexpected version_no")
        return self.dataset_record, b"{}"


def test_create_export_job_rejects_viewer_role(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: ExportRepoStub())
    client = TestClient(export_app_module.app)

    response = client.post(
        "/internal/exports/jobs",
        headers={"x-internal-role": "viewer"},
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "export_format": "jsonl",
        },
    )

    assert response.status_code == 403


def test_get_export_job_rejects_scope_mismatch(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/exports/jobs/{uuid4()}",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403


def test_get_export_job_allows_matching_scope(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/exports/jobs/{uuid4()}",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(repo.record.tenant_id),
            "x-auth-workspace-id": str(repo.record.workspace_id),
        },
    )

    assert response.status_code == 200


def test_download_export_job_rejects_scope_before_artifact_read(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/exports/jobs/{uuid4()}/download",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert repo.read_calls == 0


def test_download_export_job_allows_matching_scope(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/exports/jobs/{uuid4()}/download",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(repo.record.tenant_id),
            "x-auth-workspace-id": str(repo.record.workspace_id),
        },
    )

    assert response.status_code == 200
    assert repo.read_calls == 1


def test_list_dataset_versions_rejects_scope_mismatch(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/versions",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403


def test_list_dataset_versions_page_rejects_scope_mismatch(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/versions/page",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403


def test_list_export_jobs_page_rejects_scope_mismatch(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/jobs/page",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert repo.list_export_jobs_calls == 0


def test_download_latest_dataset_rejects_scope_before_read(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/versions/latest/download",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert repo.read_dataset_calls == 0


def test_list_dataset_versions_allows_matching_scope(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{repo.record.conversation_id}/exports/versions",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(repo.record.tenant_id),
            "x-auth-workspace-id": str(repo.record.workspace_id),
        },
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_download_latest_dataset_allows_matching_scope(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{repo.record.conversation_id}/exports/versions/latest/download",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(repo.record.tenant_id),
            "x-auth-workspace-id": str(repo.record.workspace_id),
        },
    )

    assert response.status_code == 200
    assert repo.read_dataset_calls == 1


def test_list_export_jobs_page_allows_matching_scope(monkeypatch: Any) -> None:
    repo = ExportRepoStub()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda c: repo)
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{repo.record.conversation_id}/exports/jobs/page",
        headers={
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(repo.record.tenant_id),
            "x-auth-workspace-id": str(repo.record.workspace_id),
        },
        params={"limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["job_id"] == str(repo.record.job_id)
    assert payload["has_more"] is False
    assert repo.list_export_jobs_calls == 1
