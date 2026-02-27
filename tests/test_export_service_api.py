"""API tests for export service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.export_service import app as export_app_module
from services.export_service.repository import (
    ConversationForExportNotFoundError,
    DatasetVersionRecord,
    ExportArtifactNotFoundError,
    ExportJobNotFoundError,
    ExportJobRecord,
    InvalidExportStorageKeyError,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _job_record() -> ExportJobRecord:
    ts = datetime(2026, 2, 27, 22, 0, tzinfo=timezone.utc)
    return ExportJobRecord(
        job_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        export_format="jsonl",
        status="completed",
        storage_key=str(Path("/tmp") / "dataset.jsonl"),
        row_count=3,
        manifest={"schema_version": "dataset.v1"},
        requested_by_user_id=None,
        created_at=ts,
        completed_at=ts,
    )


class SuccessRepository:
    def __init__(self):
        self.record = _job_record()

    def create_export_job(self, payload: Any) -> ExportJobRecord:
        return self.record

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        return self.record

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        return self.record, b'{"sample":1}\n'

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        ts = datetime(2026, 2, 27, 22, 1, tzinfo=timezone.utc)
        return [
            DatasetVersionRecord(
                dataset_version_id=uuid4(),
                conversation_id=conversation_id,
                version_no=2,
                export_job_id=uuid4(),
                export_format="jsonl",
                storage_key="/tmp/v2.jsonl",
                row_count=12,
                manifest={"dataset_version_no": 2},
                created_at=ts,
            ),
            DatasetVersionRecord(
                dataset_version_id=uuid4(),
                conversation_id=conversation_id,
                version_no=1,
                export_job_id=uuid4(),
                export_format="csv",
                storage_key="/tmp/v1.csv",
                row_count=10,
                manifest={"dataset_version_no": 1},
                created_at=ts,
            ),
        ]


class MissingConversationRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ConversationForExportNotFoundError(
            "Conversation not found for the given tenant/workspace"
        )

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")


class InvalidFormatRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ValueError("export_format must be one of: jsonl, csv")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")


class MissingJobRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise ExportJobNotFoundError(f"Export job {job_id} not found")

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise ExportJobNotFoundError(f"Export job {job_id} not found")

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")


class MissingArtifactRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise ExportArtifactNotFoundError("Export artifact not found")

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")


class InvalidStorageRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise InvalidExportStorageKeyError("Export storage key is outside export root")

    def list_dataset_versions(self, conversation_id: Any, limit: int) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")


def test_create_export_job_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.post(
        "/internal/exports/jobs",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "export_format": "jsonl",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["export_format"] == "jsonl"
    assert payload["row_count"] == 3


def test_create_export_job_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingConversationRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.post(
        "/internal/exports/jobs",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "export_format": "jsonl",
        },
    )

    assert response.status_code == 404


def test_create_export_job_endpoint_invalid_format(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: InvalidFormatRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.post(
        "/internal/exports/jobs",
        json={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "export_format": "parquet",
        },
    )

    assert response.status_code == 400


def test_get_export_job_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"


def test_get_export_job_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingJobRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}")

    assert response.status_code == 404


def test_download_export_job_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content == b'{"sample":1}\n'


def test_download_export_job_endpoint_missing_job(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingJobRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}/download")

    assert response.status_code == 404


def test_download_export_job_endpoint_missing_artifact(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingArtifactRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}/download")

    assert response.status_code == 404


def test_download_export_job_endpoint_invalid_storage(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: InvalidStorageRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/exports/jobs/{uuid4()}/download")

    assert response.status_code == 409


def test_list_dataset_versions_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)
    conversation_id = uuid4()

    response = client.get(
        f"/internal/conversations/{conversation_id}/exports/versions",
        params={"limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["version_no"] == 2
    assert payload[1]["version_no"] == 1
