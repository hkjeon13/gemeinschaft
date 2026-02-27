"""API tests for ingestion process endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module
from services.data_ingestion.ingestion_worker import ProcessSourceResult, SourceNotFoundError


class DummyConnection:
    def close(self) -> None:
        return None


class DummyStorage:
    provider = "local_fs"

    def put_object(self, key: str, data: bytes, content_type: str | None = None) -> None:
        del key, data, content_type

    def get_object(self, key: str) -> bytes:
        raise FileNotFoundError(key)

    def delete_object(self, key: str) -> None:
        del key


class SuccessWorker:
    def __init__(self, source_id: str):
        self._source_id = source_id

    def process_source(self, source_id: Any) -> ProcessSourceResult:
        assert str(source_id) == self._source_id
        return ProcessSourceResult(
            source_id=source_id,
            status="processed",
            chunk_count=3,
        )


class DlqWorker:
    def process_source(self, source_id: Any) -> ProcessSourceResult:
        return ProcessSourceResult(
            source_id=source_id,
            status="dlq",
            chunk_count=0,
            dlq_id=44,
            error_type="UnsupportedSourceError",
            error_message="unsupported",
        )


class NotFoundWorker:
    def process_source(self, source_id: Any) -> ProcessSourceResult:
        raise SourceNotFoundError(f"Source document {source_id} not found")


def test_process_source_endpoint_success(monkeypatch: Any) -> None:
    source_id = str(uuid4())
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_storage",
        lambda: DummyStorage(),
    )
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_ingestion_worker",
        lambda connection, storage: SuccessWorker(source_id),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(f"/internal/sources/{source_id}/process")

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert response.json()["chunk_count"] == 3


def test_process_source_endpoint_dlq(monkeypatch: Any) -> None:
    source_id = str(uuid4())
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: DummyStorage())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_ingestion_worker",
        lambda connection, storage: DlqWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(f"/internal/sources/{source_id}/process")

    assert response.status_code == 200
    assert response.json()["status"] == "dlq"
    assert response.json()["dlq_id"] == 44
    assert response.json()["error_type"] == "UnsupportedSourceError"


def test_process_source_endpoint_not_found(monkeypatch: Any) -> None:
    source_id = str(uuid4())
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: DummyStorage())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_ingestion_worker",
        lambda connection, storage: NotFoundWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(f"/internal/sources/{source_id}/process")

    assert response.status_code == 404
