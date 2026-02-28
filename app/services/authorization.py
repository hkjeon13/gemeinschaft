import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, status

from .auth import JwtContext, require_jwt, scopes_from_claims
from .security_audit import emit_security_event


@dataclass
class AccessContext:
    subject: str
    tenant: str
    role: Optional[str]
    scopes: Set[str]
    claims: Dict[str, Any]


def _default_policies() -> Dict[str, Dict[str, Any]]:
    return {
        "conversation:list": {
            "required_scopes": ["conversation:read"],
            "enforce_tenant": True,
        },
        "conversation:get": {
            "required_scopes": ["conversation:read"],
            "enforce_tenant": True,
        },
        "conversation:create": {
            "required_scopes": ["conversation:write"],
            "enforce_tenant": True,
        },
        "admin:user:list": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:user:create": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:user:update": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:user:delete": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:model:list": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:model:create": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:model:update": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
        "admin:model:delete": {
            "required_scopes": [],
            "required_roles": ["admin"],
            "enforce_tenant": True,
        },
    }


def _load_authz_policies() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("AUTHZ_POLICIES_JSON", "").strip()
    if not raw:
        return _default_policies()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTHZ_POLICIES_JSON must be valid JSON.",
        )

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTHZ_POLICIES_JSON must be a JSON object.",
        )

    policies: Dict[str, Dict[str, Any]] = {}
    for action, policy in parsed.items():
        if not isinstance(action, str) or not action:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTHZ_POLICIES_JSON action names must be non-empty strings.",
            )
        if not isinstance(policy, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTHZ policy '{action}' must be an object.",
            )

        required_scopes = policy.get("required_scopes", [])
        if isinstance(required_scopes, str):
            required_scopes = [scope for scope in required_scopes.split(" ") if scope]

        if not isinstance(required_scopes, list):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTHZ policy '{action}' required_scopes must be a list or space-delimited string.",
            )

        normalized_scopes: List[str] = []
        for scope in required_scopes:
            if not isinstance(scope, str) or not scope:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTHZ policy '{action}' required_scopes values must be non-empty strings.",
                )
            normalized_scopes.append(scope)

        required_roles = policy.get("required_roles", [])
        if isinstance(required_roles, str):
            required_roles = [role for role in required_roles.split(" ") if role]

        if not isinstance(required_roles, list):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTHZ policy '{action}' required_roles must be a list or space-delimited string.",
            )

        normalized_roles: List[str] = []
        for role in required_roles:
            if not isinstance(role, str) or not role:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTHZ policy '{action}' required_roles values must be non-empty strings.",
                )
            normalized_roles.append(role)

        enforce_tenant = bool(policy.get("enforce_tenant", True))

        resource_prefix = policy.get("resource_prefix")
        if resource_prefix is not None and (not isinstance(resource_prefix, str) or not resource_prefix):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTHZ policy '{action}' resource_prefix must be a non-empty string.",
            )

        policies[action] = {
            "required_scopes": normalized_scopes,
            "required_roles": normalized_roles,
            "enforce_tenant": enforce_tenant,
            "resource_prefix": resource_prefix,
        }

    return policies


def require_access_context(jwt_ctx: JwtContext = Depends(require_jwt)) -> AccessContext:
    claims = jwt_ctx.claims

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid JWT subject.")

    tenant = claims.get("tenant")
    if not isinstance(tenant, str) or not tenant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid JWT tenant.")

    role = claims.get("role")
    if role is not None and not isinstance(role, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid JWT role.")

    scopes = set(scopes_from_claims(claims))

    return AccessContext(
        subject=subject,
        tenant=tenant,
        role=role,
        scopes=scopes,
        claims=claims,
    )


def authorize_action(access: AccessContext, action: str, resource_id: Optional[str] = None) -> None:
    policies = _load_authz_policies()
    policy = policies.get(action)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authorization policy is not configured for action '{action}'.",
        )

    required_scopes = policy["required_scopes"]
    missing = sorted([scope for scope in required_scopes if scope not in access.scopes])
    if missing:
        emit_security_event(
            event_type="authorization_denied",
            outcome="deny",
            action=action,
            reason="missing_scope",
            subject=access.subject,
            tenant=access.tenant,
            missing_scopes=missing,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope.",
        )

    required_roles = policy.get("required_roles", [])
    if required_roles:
        current_role = access.role or ""
        if current_role not in required_roles:
            emit_security_event(
                event_type="authorization_denied",
                outcome="deny",
                action=action,
                reason="missing_role",
                subject=access.subject,
                tenant=access.tenant,
                required_roles=required_roles,
                current_role=current_role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role.",
            )

    if policy["enforce_tenant"] and not access.tenant:
        emit_security_event(
            event_type="authorization_denied",
            outcome="deny",
            action=action,
            reason="missing_tenant",
            subject=access.subject,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is required.",
        )

    resource_prefix = policy.get("resource_prefix")
    if resource_prefix and resource_id is not None and not resource_id.startswith(resource_prefix):
        emit_security_event(
            event_type="authorization_denied",
            outcome="deny",
            action=action,
            reason="resource_prefix_mismatch",
            subject=access.subject,
            tenant=access.tenant,
            resource_id=resource_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource policy denied.",
        )


def validate_authorization_settings() -> None:
    _load_authz_policies()
