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
    ExportJobNotFoundError,
    ExportJobRecord,
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


class MissingConversationRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ConversationForExportNotFoundError(
            "Conversation not found for the given tenant/workspace"
        )

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")


class InvalidFormatRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise ValueError("export_format must be one of: jsonl, csv")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise AssertionError("not used")


class MissingJobRepository:
    def create_export_job(self, payload: Any) -> ExportJobRecord:
        raise AssertionError("not used")

    def get_export_job(self, job_id: Any) -> ExportJobRecord:
        raise ExportJobNotFoundError(f"Export job {job_id} not found")


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
