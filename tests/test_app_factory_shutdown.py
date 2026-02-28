"""Tests for service app factory shutdown hooks."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from services.shared.app_factory import build_service_app


def test_shutdown_closes_shared_db_pools(monkeypatch: Any) -> None:
    calls = {"count": 0}

    def _fake_close_all_pools() -> None:
        calls["count"] += 1

    monkeypatch.setattr(
        "services.shared.app_factory.close_all_db_pools",
        _fake_close_all_pools,
    )
    app = build_service_app("probe")

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200

    assert calls["count"] == 1
