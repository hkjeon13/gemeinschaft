"""Shared internal auth and request scope helpers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request


@dataclass(frozen=True)
class AuthContext:
    principal_id: str | None
    role: str
    tenant_id: UUID | None
    workspace_id: UUID | None
    token_authenticated: bool


def get_auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if isinstance(context, AuthContext):
        return context
    return AuthContext(
        principal_id=None,
        role="system",
        tenant_id=None,
        workspace_id=None,
        token_authenticated=False,
    )


def enforce_role(auth: AuthContext, *, allowed_roles: set[str]) -> None:
    normalized_allowed = {role.strip().lower() for role in allowed_roles if role.strip()}
    if auth.role.lower() in normalized_allowed:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "message": "role is not allowed for this operation",
            "role": auth.role,
            "allowed_roles": sorted(normalized_allowed),
        },
    )


def enforce_scope(
    auth: AuthContext,
    *,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    if auth.tenant_id is not None and tenant_id is not None and auth.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "tenant scope mismatch",
                "auth_tenant_id": str(auth.tenant_id),
                "resource_tenant_id": str(tenant_id),
            },
        )
    if (
        auth.workspace_id is not None
        and workspace_id is not None
        and auth.workspace_id != workspace_id
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "workspace scope mismatch",
                "auth_workspace_id": str(auth.workspace_id),
                "resource_workspace_id": str(workspace_id),
            },
        )
