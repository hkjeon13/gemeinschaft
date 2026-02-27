"""API tests for source upload ingestion path."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from services.data_ingestion import app as data_ingestion_app_module


class FakeStorage:
    provider = "local_fs"

    def __init__(self):
        self.saved: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes, content_type: str | None = None) -> None:
        del content_type
        self.saved[key] = data


class FakeConnection:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.insert_params: tuple[Any, ...] | None = None
        self._last_fetch: Any = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "insert into source_document" not in normalized_sql:
            raise AssertionError(f"Unexpected SQL in test fake: {normalized_sql}")
        assert params is not None
        self.insert_params = params
        self._last_fetch = (
            UUID(params[0]),
            datetime(2026, 2, 27, 16, 30, tzinfo=timezone.utc),
        )

    def fetchone(self) -> Any:
        return self._last_fetch

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def test_upload_source_success(monkeypatch: Any) -> None:
    fake_connection = FakeConnection()
    fake_storage = FakeStorage()
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: fake_connection)
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: fake_storage)
    client = TestClient(data_ingestion_app_module.app)

    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    content = b"hello source document"

    response = client.post(
        "/internal/sources/upload",
        data={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "source_type": "upload",
            "metadata": json.dumps({"origin": "test-suite", "priority": 1}),
        },
        files={"file": ("sample.txt", content, "text/plain")},
    )

    assert response.status_code == 201
    payload = response.json()

    assert payload["source_id"]
    assert payload["byte_size"] == len(content)
    assert payload["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["storage_key"] in fake_storage.saved
    assert fake_storage.saved[payload["storage_key"]] == content

    assert fake_connection.insert_params is not None
    assert fake_connection.insert_params[1] == tenant_id
    assert fake_connection.insert_params[2] == workspace_id
    assert fake_connection.insert_params[3] == "upload"
    assert fake_connection.insert_params[4] == "sample.txt"
    assert fake_connection.insert_params[6] == len(content)
    assert fake_connection.insert_params[7] == hashlib.sha256(content).hexdigest()
    assert json.loads(fake_connection.insert_params[10]) == {
        "origin": "test-suite",
        "priority": 1,
    }
    assert fake_connection.commit_calls == 1
    assert fake_connection.rollback_calls == 0
    assert fake_connection.close_calls == 1


def test_upload_source_rejects_bad_metadata(monkeypatch: Any) -> None:
    monkeypatch.setattr(data_ingestion_app_module, "_build_storage", lambda: FakeStorage())
    monkeypatch.setattr(data_ingestion_app_module, "_connect", lambda: FakeConnection())
    client = TestClient(data_ingestion_app_module.app)

    response = client.post(
        "/internal/sources/upload",
        data={
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "metadata": "not-json",
        },
        files={"file": ("sample.txt", b"abc", "text/plain")},
    )

    assert response.status_code == 400
    assert "metadata must be valid json object" in response.json()["detail"].lower()
