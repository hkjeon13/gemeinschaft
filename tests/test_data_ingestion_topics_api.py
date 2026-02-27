"""API tests for topic clustering endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module
from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.topic_worker import TopicSourceResult


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ClusteredWorker:
    def cluster_source(self, source_id: Any) -> TopicSourceResult:
        return TopicSourceResult(
            source_id=source_id,
            status="clustered",
            topic_count=2,
            link_count=5,
        )


class DlqWorker:
    def cluster_source(self, source_id: Any) -> TopicSourceResult:
        return TopicSourceResult(
            source_id=source_id,
            status="dlq",
            topic_count=0,
            link_count=0,
            dlq_id=901,
            error_type="RuntimeError",
            error_message="cluster failed",
        )


class NotFoundWorker:
    def cluster_source(self, source_id: Any) -> TopicSourceResult:
        raise SourceNotFoundError(f"Source document {source_id} not found")


def test_cluster_topics_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_topic_worker",
        lambda connection: ClusteredWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/topics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "clustered"
    assert payload["topic_count"] == 2
    assert payload["link_count"] == 5


def test_cluster_topics_endpoint_dlq(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_topic_worker",
        lambda connection: DlqWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/topics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "dlq"
    assert payload["dlq_id"] == 901
    assert payload["error_type"] == "RuntimeError"


def test_cluster_topics_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        data_ingestion_app_module,
        "_build_topic_worker",
        lambda connection: NotFoundWorker(),
    )
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/topics")

    assert response.status_code == 404


def test_cluster_topics_endpoint_rejects_invalid_threshold(monkeypatch: Any) -> None:
    connection = DummyConnection()
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: connection)
    monkeypatch.setenv("TOPIC_SIMILARITY_THRESHOLD", "1.5")
    client = TestClient(data_ingestion_app_module.app)
    source_id = str(uuid4())

    response = client.post(f"/internal/sources/{source_id}/topics")

    assert response.status_code == 500
    assert "topic_similarity_threshold" in response.json()["detail"].lower()
    assert connection.closed is True
