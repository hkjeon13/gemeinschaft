"""API tests for source embedding endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module
from services.data_ingestion.embedding_worker import EmbedSourceResult
from services.data_ingestion.ingestion_worker import SourceNotFoundError


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class EmbeddedWorker:
    def process(self, source_id: Any) -> EmbedSourceResult:
        return EmbedSourceResult(
            source_id=source_id,
            status="embedded",
            chunk_count=3,
            embedding_count=3,
        )

    def embed_source(self, source_id: Any) -> EmbedSourceResult:
        return self.process(source_id)


class DlqWorker:
    def embed_source(self, source_id: Any) -> EmbedSourceResult:
        return EmbedSourceResult(
            source_id=source_id,
            status="dlq",
            chunk_count=0,
            embedding_count=0,
            dlq_id=77,
            error_type="RuntimeError",
            error_message="boom",
        )


class NotFoundWorker:
    def embed_source(self, source_id: Any) -> EmbedSourceResult:
        raise SourceNotFoundError(f"Source document {source_id} not found")


def test_embed_source_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_embedding_worker",
        lambda connection: EmbeddedWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/embed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "embedded"
    assert payload["chunk_count"] == 3
    assert payload["embedding_count"] == 3


def test_embed_source_endpoint_dlq(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_embedding_worker",
        lambda connection: DlqWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/embed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "dlq"
    assert payload["dlq_id"] == 77
    assert payload["error_type"] == "RuntimeError"


def test_embed_source_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_embedding_worker",
        lambda connection: NotFoundWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/embed")

    assert response.status_code == 404


def test_embed_source_endpoint_rejects_invalid_embedding_dim(monkeypatch: Any) -> None:
    connection = DummyConnection()
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: connection)
    monkeypatch.setenv("EMBEDDING_DIM", "64")
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/embed")

    assert response.status_code == 500
    assert "embedding_dim must be 128" in response.json()["detail"].lower()
    assert connection.closed is True
