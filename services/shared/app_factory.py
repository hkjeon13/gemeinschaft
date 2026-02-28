"""Small FastAPI app factory used by all scaffold services."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from time import perf_counter
from typing import AsyncIterator
from uuid import UUID, uuid4

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.shared.auth import AuthContext
from services.shared.db import close_all_db_pools


_LOGGER = logging.getLogger("service.request")


def _parse_optional_uuid(raw: str | None, header_name: str) -> UUID | None:
    if raw is None or not raw.strip():
        return None
    try:
        return UUID(raw.strip())
    except ValueError:
        raise ValueError(f"{header_name} must be a valid UUID")


def build_service_app(service_name: str) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            close_all_db_pools()

    app = FastAPI(
        title=f"{service_name} service",
        version="0.1.0",
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def internal_auth_middleware(request: Request, call_next) -> Response:
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)

        internal_api_token = os.getenv("INTERNAL_API_TOKEN")
        provided_token = request.headers.get("x-internal-api-token")
        token_authenticated = False

        if internal_api_token:
            if provided_token != internal_api_token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "invalid or missing internal api token"},
                )
            token_authenticated = True

        role = request.headers.get("x-internal-role", "system").strip().lower() or "system"
        principal_id = request.headers.get("x-internal-principal-id")
        try:
            tenant_id = _parse_optional_uuid(
                request.headers.get("x-auth-tenant-id"), "x-auth-tenant-id"
            )
            workspace_id = _parse_optional_uuid(
                request.headers.get("x-auth-workspace-id"), "x-auth-workspace-id"
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        request.state.auth_context = AuthContext(
            principal_id=principal_id.strip() if principal_id else None,
            role=role,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            token_authenticated=token_authenticated,
        )

        return await call_next(request)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request.state.request_id = request_id

        start = perf_counter()
        response = await call_next(request)
        latency_ms = (perf_counter() - start) * 1000.0
        response.headers["x-request-id"] = request_id
        _LOGGER.info(
            "service=%s request_id=%s method=%s path=%s status=%s latency_ms=%.2f",
            service_name,
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
        )
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready", "service": service_name}

    return app
