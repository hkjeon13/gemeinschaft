"""Tests for shared internal auth and request context middleware."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.testclient import TestClient

from services.shared.app_factory import build_service_app
from services.shared.auth import AuthContext, get_auth_context


def _build_probe_app():
    app = build_service_app("probe")

    @app.get("/internal/probe/auth")
    def probe_auth(request: Request) -> dict[str, Any]:
        ctx: AuthContext = get_auth_context(request)
        return {
            "role": ctx.role,
            "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None,
            "workspace_id": str(ctx.workspace_id) if ctx.workspace_id else None,
            "token_authenticated": ctx.token_authenticated,
            "request_id": request.state.request_id,
        }

    return app


def test_health_endpoint_bypasses_internal_auth(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    app = _build_probe_app()
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["service"] == "probe"


def test_internal_auth_requires_token_when_configured(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    app = _build_probe_app()
    client = TestClient(app)

    response = client.get("/internal/probe/auth")
    assert response.status_code == 401
    assert response.headers.get("x-request-id")

    response = client.get(
        "/internal/probe/auth",
        headers={
            "x-internal-api-token": "wrong-token",
            "x-request-id": "req-unauth-1",
        },
    )
    assert response.status_code == 401
    assert response.headers.get("x-request-id") == "req-unauth-1"

    response = client.get(
        "/internal/probe/auth", headers={"x-internal-api-token": "secret-token"}
    )
    assert response.status_code == 200
    assert response.json()["token_authenticated"] is True


def test_internal_auth_parses_scope_headers(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    app = _build_probe_app()
    client = TestClient(app)
    tenant_id = uuid4()
    workspace_id = uuid4()

    response = client.get(
        "/internal/probe/auth",
        headers={
            "x-internal-api-token": "secret-token",
            "x-internal-role": "viewer",
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "viewer"
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["workspace_id"] == str(workspace_id)


def test_internal_auth_rejects_invalid_scope_header(monkeypatch: Any) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret-token")
    app = _build_probe_app()
    client = TestClient(app)

    response = client.get(
        "/internal/probe/auth",
        headers={
            "x-internal-api-token": "secret-token",
            "x-auth-tenant-id": "not-a-uuid",
            "x-request-id": "req-bad-scope-1",
        },
    )

    assert response.status_code == 400
    assert response.headers.get("x-request-id") == "req-bad-scope-1"


def test_request_id_round_trip(monkeypatch: Any) -> None:
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    app = _build_probe_app()
    client = TestClient(app)

    response = client.get("/internal/probe/auth", headers={"x-request-id": "req-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-123"
    assert response.json()["request_id"] == "req-123"
