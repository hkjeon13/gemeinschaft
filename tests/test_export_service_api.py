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
    DatasetVersionNotFoundError,
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
        self._page_job_latest_id = uuid4()
        self._page_job_old_id = uuid4()
        self._page_job_latest_at = datetime(2026, 2, 27, 22, 3, tzinfo=timezone.utc)
        self._page_job_old_at = datetime(2026, 2, 27, 22, 2, tzinfo=timezone.utc)
        self.list_export_jobs_calls: list[dict[str, Any]] = []

    def create_export_job(self, payload: Any) -> ExportJobRecord:
        return self.record

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        return self.record

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        return self.record, b'{"sample":1}\n'

    def list_export_jobs(
        self,
        *,
        conversation_id: Any,
        limit: int,
        before_created_at: datetime | None = None,
        before_job_id: Any | None = None,
    ) -> list[ExportJobRecord]:
        self.list_export_jobs_calls.append(
            {
                "conversation_id": conversation_id,
                "limit": limit,
                "before_created_at": before_created_at,
                "before_job_id": before_job_id,
            }
        )
        rows = [
            ExportJobRecord(
                job_id=self._page_job_latest_id,
                tenant_id=self.record.tenant_id,
                workspace_id=self.record.workspace_id,
                conversation_id=conversation_id,
                export_format="jsonl",
                status="completed",
                storage_key="/tmp/page-latest.jsonl",
                row_count=3,
                manifest={"schema_version": "dataset.v1"},
                requested_by_user_id=None,
                created_at=self._page_job_latest_at,
                completed_at=self._page_job_latest_at,
            ),
            ExportJobRecord(
                job_id=self._page_job_old_id,
                tenant_id=self.record.tenant_id,
                workspace_id=self.record.workspace_id,
                conversation_id=conversation_id,
                export_format="csv",
                status="completed",
                storage_key="/tmp/page-old.csv",
                row_count=2,
                manifest={"schema_version": "dataset.v1"},
                requested_by_user_id=None,
                created_at=self._page_job_old_at,
                completed_at=self._page_job_old_at,
            ),
        ]
        if before_created_at is not None and before_job_id is not None:
            rows = [
                row
                for row in rows
                if row.created_at < before_created_at
                or (row.created_at == before_created_at and row.job_id < before_job_id)
            ]
        return rows[:limit]

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        ts = datetime(2026, 2, 27, 22, 1, tzinfo=timezone.utc)
        rows = [
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
        if before_version_no is None:
            return rows[:limit]
        return [row for row in rows if row.version_no < before_version_no][:limit]

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        return self.list_dataset_versions(conversation_id, limit=1)[0]

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        if version_no < 1:
            raise ValueError("version_no must be >= 1")
        for row in self.list_dataset_versions(conversation_id, limit=20):
            if row.version_no == version_no:
                return row
        raise DatasetVersionNotFoundError("Dataset version not found")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        record = (
            self.get_latest_dataset_version(conversation_id)
            if version_no is None
            else self.get_dataset_version(conversation_id, version_no)
        )
        return record, b"dataset-version-bytes"


class MissingConversationRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ConversationForExportNotFoundError(
            "Conversation not found for the given tenant/workspace"
        )

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise AssertionError("not used")


class InvalidFormatRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ValueError("export_format must be one of: jsonl, csv")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise AssertionError("not used")


class MissingJobRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise ExportJobNotFoundError(f"Export job {job_id} not found")

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise ExportJobNotFoundError(f"Export job {job_id} not found")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise AssertionError("not used")


class MissingArtifactRepository:
    def __init__(self):
        self.record = _job_record()

    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        return self.record

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise ExportArtifactNotFoundError("Export artifact not found")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise ExportArtifactNotFoundError("Export artifact not found")


class InvalidStorageRepository:
    def __init__(self):
        self.record = _job_record()

    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        return self.record

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise InvalidExportStorageKeyError("Export storage key is outside export root")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        raise AssertionError("not used")

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise AssertionError("not used")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise InvalidExportStorageKeyError("Export storage key is outside export root")


class MissingVersionRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def read_export_artifact(self, job_id: Any) -> tuple[ExportJobRecord, bytes]:
        raise AssertionError("not used")

    def list_dataset_versions(
        self,
        conversation_id: Any,
        limit: int,
        before_version_no: int | None = None,
    ) -> list[DatasetVersionRecord]:
        return []

    def get_latest_dataset_version(self, conversation_id: Any) -> DatasetVersionRecord:
        raise DatasetVersionNotFoundError("No dataset versions found")

    def get_dataset_version(self, conversation_id: Any, version_no: int) -> DatasetVersionRecord:
        raise DatasetVersionNotFoundError("Dataset version not found")

    def read_dataset_version_artifact(
        self, conversation_id: Any, version_no: int | None = None
    ) -> tuple[DatasetVersionRecord, bytes]:
        raise DatasetVersionNotFoundError("Dataset version not found")


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


def test_list_export_jobs_page_endpoint_success(monkeypatch: Any) -> None:
    repo = SuccessRepository()
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(export_app_module, "_build_repository", lambda connection: repo)
    client = TestClient(export_app_module.app)
    conversation_id = uuid4()

    first = client.get(
        f"/internal/conversations/{conversation_id}/exports/jobs/page",
        params={"limit": 1},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["job_id"] == str(repo._page_job_latest_id)
    assert first_payload["next_cursor"] == (
        f"j:2026-02-27T22:03:00Z|{repo._page_job_latest_id}"
    )
    assert first_payload["has_more"] is True
    assert repo.list_export_jobs_calls[0]["limit"] == 2
    assert repo.list_export_jobs_calls[0]["before_created_at"] is None

    second = client.get(
        f"/internal/conversations/{conversation_id}/exports/jobs/page",
        params={"limit": 1, "cursor": first_payload["next_cursor"]},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["job_id"] == str(repo._page_job_old_id)
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False
    assert repo.list_export_jobs_calls[1]["limit"] == 2
    assert repo.list_export_jobs_calls[1]["before_created_at"] == repo._page_job_latest_at
    assert repo.list_export_jobs_calls[1]["before_job_id"] == repo._page_job_latest_id


def test_list_export_jobs_page_endpoint_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/jobs/page",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 400


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


def test_list_dataset_versions_page_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)
    conversation_id = uuid4()

    first = client.get(
        f"/internal/conversations/{conversation_id}/exports/versions/page",
        params={"limit": 1},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["version_no"] == 2
    assert first_payload["next_cursor"] == "v:2"
    assert first_payload["has_more"] is True

    second = client.get(
        f"/internal/conversations/{conversation_id}/exports/versions/page",
        params={"limit": 1, "cursor": "v:2"},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["version_no"] == 1
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False


def test_list_dataset_versions_page_endpoint_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/versions/page",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 400


def test_get_latest_dataset_version_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/exports/versions/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version_no"] == 2


def test_get_dataset_version_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/exports/versions/1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version_no"] == 1


def test_get_dataset_version_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingVersionRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/exports/versions/1")

    assert response.status_code == 404


def test_get_dataset_version_endpoint_invalid_version(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/exports/versions/0")

    assert response.status_code == 400


def test_download_latest_dataset_version_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: SuccessRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/exports/versions/latest/download"
    )

    assert response.status_code == 200
    assert response.content == b"dataset-version-bytes"


def test_download_dataset_version_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(export_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        export_app_module,
        "_build_repository",
        lambda connection: MissingVersionRepository(),
    )
    client = TestClient(export_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/exports/versions/7/download")

    assert response.status_code == 404
