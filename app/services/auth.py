import base64
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError

from .auth_user_store import StoredAuthUser, get_auth_user_store, initialize_auth_user_store
from .security_audit import emit_security_event
from .security_state import (
    get_security_state_backend,
    login_rate_limit_settings,
    validate_security_state_settings,
)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class JwtContext:
    token: str
    claims: Dict[str, Any]


@dataclass
class AuthUser:
    username: str
    role: str
    tenant: str
    scopes: List[str]


@dataclass
class AuthUserRecord:
    password_hash: Optional[str]
    plain_password: Optional[str]
    role: str
    tenant: str
    scopes: List[str]


@dataclass
class JwtKeyset:
    algorithm: str
    active_kid: str
    signing_keys: Dict[str, str]
    verification_keys: Dict[str, str]
    jwks: Dict[str, Any]


def _raise_unauthorized(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _jwt_algorithm() -> str:
    algorithm = os.getenv("JWT_ALGORITHM", "RS256").strip().upper()
    if algorithm != "RS256":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_ALGORITHM must be RS256.",
        )
    return algorithm


def _jwt_issuer() -> str:
    return os.getenv("JWT_ISSUER", "gemeinschaft-api")


def _jwt_audience() -> str:
    return os.getenv("JWT_AUDIENCE", "gemeinschaft-clients")


def _normalize_pem(value: str) -> str:
    return value.replace("\\n", "\n")


def _load_json_object_from_sources(
    label: str,
    file_env: Optional[str],
    json_env: Optional[str],
    required: bool,
) -> Dict[str, Any]:
    file_path = os.getenv(file_env, "").strip() if file_env else ""
    raw_json = os.getenv(json_env, "").strip() if json_env else ""

    source_name = None
    payload = ""

    if file_path:
        source_name = f"{file_env} ({file_path})"
        try:
            with open(file_path, "r", encoding="utf-8") as fp:
                payload = fp.read().strip()
        except OSError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{label} file cannot be read: {file_path}",
            )
    elif raw_json:
        source_name = json_env
        payload = raw_json

    if not source_name:
        if required:
            source_hint = " or ".join([name for name in [file_env, json_env] if name])
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{label} must be configured via {source_hint}.",
            )
        return {}

    if not payload:
        if required:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{label} source is empty: {source_name}",
            )
        return {}

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{label} must be valid JSON: {source_name}",
        )

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{label} must be a JSON object: {source_name}",
        )

    return parsed


def _get_active_kid(candidates: Dict[str, str]) -> str:
    active_kid = os.getenv("JWT_ACTIVE_KID", "").strip()
    if not active_kid:
        return next(iter(candidates))

    if active_kid not in candidates:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_ACTIVE_KID must exist in configured signing keys.",
        )
    return active_kid


def _load_private_key_pem(name: str, key_pem: str):
    pem = _normalize_pem(key_pem)
    try:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be a valid PEM private key.",
        )


def _load_public_key_pem(name: str, key_pem: str):
    pem = _normalize_pem(key_pem)
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be a valid PEM public key.",
        )


def _public_pem_from_private(private_pem: str) -> str:
    key_obj = _load_private_key_pem("JWT private key", private_pem)
    if not isinstance(key_obj, rsa.RSAPrivateKey):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private keys must be RSA private keys for RS256.",
        )
    public_pem = key_obj.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem.decode("utf-8")


def _to_base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _rsa_public_jwk(kid: str, public_pem: str) -> Dict[str, Any]:
    key_obj = _load_public_key_pem(f"JWT public key '{kid}'", public_pem)
    if not isinstance(key_obj, rsa.RSAPublicKey):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"JWT public key '{kid}' must be RSA for RS256.",
        )

    numbers = key_obj.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _to_base64url_uint(numbers.n),
        "e": _to_base64url_uint(numbers.e),
    }


def _load_jwt_keyset() -> JwtKeyset:
    algorithm = _jwt_algorithm()
    raw_private = _load_json_object_from_sources(
        label="JWT private keys",
        file_env="JWT_PRIVATE_KEYS_FILE",
        json_env="JWT_PRIVATE_KEYS_JSON",
        required=True,
    )

    signing_keys: Dict[str, str] = {}
    for kid, key in raw_private.items():
        if not isinstance(kid, str) or not kid:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT private key IDs must be non-empty strings.",
            )
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"JWT private key '{kid}' must be a non-empty PEM string.",
            )

        normalized = _normalize_pem(key)
        _load_private_key_pem(f"JWT private key '{kid}'", normalized)
        signing_keys[kid] = normalized

    active_kid = _get_active_kid(signing_keys)

    raw_public = _load_json_object_from_sources(
        label="JWT public keys",
        file_env="JWT_PUBLIC_KEYS_FILE",
        json_env="JWT_PUBLIC_KEYS_JSON",
        required=False,
    )
    verification_keys: Dict[str, str] = {}

    for kid, key in raw_public.items():
        if not isinstance(kid, str) or not kid:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT public key IDs must be non-empty strings.",
            )
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"JWT public key '{kid}' must be a non-empty PEM string.",
            )

        normalized = _normalize_pem(key)
        _load_public_key_pem(f"JWT public key '{kid}'", normalized)
        verification_keys[kid] = normalized

    for kid, private_pem in signing_keys.items():
        verification_keys.setdefault(kid, _public_pem_from_private(private_pem))

    jwks = {"keys": [_rsa_public_jwk(kid, public_pem) for kid, public_pem in verification_keys.items()]}

    return JwtKeyset(
        algorithm=algorithm,
        active_kid=active_kid,
        signing_keys=signing_keys,
        verification_keys=verification_keys,
        jwks=jwks,
    )


def get_jwks_document() -> Dict[str, Any]:
    return _load_jwt_keyset().jwks


def _access_token_expires_minutes() -> int:
    raw = os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "60")
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_ACCESS_TOKEN_EXPIRES_MINUTES must be an integer.",
        )
    if value <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_ACCESS_TOKEN_EXPIRES_MINUTES must be greater than 0.",
        )
    return value


def _refresh_token_expires_days() -> int:
    raw = os.getenv("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "14")
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_REFRESH_TOKEN_EXPIRES_DAYS must be an integer.",
        )
    if value <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_REFRESH_TOKEN_EXPIRES_DAYS must be greater than 0.",
        )
    return value


def access_token_expires_seconds() -> int:
    return _access_token_expires_minutes() * 60


def refresh_token_expires_seconds() -> int:
    return _refresh_token_expires_days() * 24 * 60 * 60


def _default_tenant() -> str:
    tenant = os.getenv("AUTH_DEFAULT_TENANT", "default").strip()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_DEFAULT_TENANT must be a non-empty string.",
        )
    return tenant


def _normalize_scope_values(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values:
        scope = value.strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        normalized.append(scope)
    return normalized


def _parse_scopes(value: Any, source_name: str) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return _normalize_scope_values(value.split(" "))

    if isinstance(value, list):
        raw_scopes: List[str] = []
        for scope in value:
            if not isinstance(scope, str):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{source_name} scope values must be strings.",
                )
            raw_scopes.append(scope)
        return _normalize_scope_values(raw_scopes)

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"{source_name} scopes must be a list or space-delimited string.",
    )


def _default_scopes() -> List[str]:
    raw = os.getenv("AUTH_DEFAULT_SCOPES", "conversation:read conversation:write")
    return _parse_scopes(raw, "AUTH_DEFAULT_SCOPES")


def _allow_plaintext_passwords() -> bool:
    raw = os.getenv("AUTH_ALLOW_PLAINTEXT_PASSWORDS", "false").strip().lower()
    return raw in ("1", "true", "yes", "y")


def _trust_proxy_headers() -> bool:
    raw = os.getenv("AUTH_TRUST_PROXY_HEADERS", "false").strip().lower()
    return raw in ("1", "true", "yes", "y")


def _validate_bcrypt_hash_or_raise(username: str, password_hash: str) -> None:
    try:
        bcrypt.checkpw(b"__probe_password__", password_hash.encode("utf-8"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AUTH user '{username}' has invalid bcrypt hash.",
        )


def _load_auth_users() -> Dict[str, AuthUserRecord]:
    parsed = _load_json_object_from_sources(
        label="AUTH users",
        file_env="AUTH_USERS_FILE",
        json_env="AUTH_USERS_JSON",
        required=False,
    )
    if not parsed:
        return {}

    users: Dict[str, AuthUserRecord] = {}
    for username, config in parsed.items():
        if not isinstance(username, str) or not username:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTH user keys must be non-empty usernames.",
            )

        role = "user"
        tenant = _default_tenant()
        scopes = _default_scopes()
        password_hash: Optional[str] = None
        plain_password: Optional[str] = None

        if isinstance(config, str):
            password_hash = config
        elif isinstance(config, dict):
            role = config.get("role", "user")
            tenant = config.get("tenant", _default_tenant())
            scopes = _parse_scopes(config.get("scopes", _default_scopes()), f"AUTH user '{username}'")
            password_hash = config.get("password_hash")
            plain_password = config.get("password")
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH user '{username}' has invalid config.",
            )

        if not isinstance(role, str) or not role:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH user '{username}' role must be a non-empty string.",
            )

        if not isinstance(tenant, str) or not tenant:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH user '{username}' tenant must be a non-empty string.",
            )

        if password_hash is not None:
            if not isinstance(password_hash, str) or not password_hash:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTH user '{username}' password_hash must be a non-empty string.",
                )
            _validate_bcrypt_hash_or_raise(username, password_hash)
            users[username] = AuthUserRecord(
                password_hash=password_hash,
                plain_password=None,
                role=role,
                tenant=tenant,
                scopes=scopes,
            )
            continue

        if plain_password is not None:
            if not isinstance(plain_password, str) or not plain_password:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTH user '{username}' password must be a non-empty string.",
                )
            if not _allow_plaintext_passwords():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"AUTH user '{username}' uses plaintext password. "
                        "Use password_hash (bcrypt) or set AUTH_ALLOW_PLAINTEXT_PASSWORDS=true only for development."
                    ),
                )
            users[username] = AuthUserRecord(
                password_hash=None,
                plain_password=plain_password,
                role=role,
                tenant=tenant,
                scopes=scopes,
            )
            continue

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AUTH user '{username}' must define password_hash.",
        )

    return users


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _seed_users_for_store(seed_config: Dict[str, AuthUserRecord]) -> Dict[str, StoredAuthUser]:
    seeded: Dict[str, StoredAuthUser] = {}
    for username, config in seed_config.items():
        password_hash = config.password_hash
        if password_hash is None and config.plain_password is not None:
            password_hash = hash_password(config.plain_password)

        if password_hash is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH user '{username}' must define password_hash or password.",
            )

        seeded[username] = StoredAuthUser(
            username=username,
            password_hash=password_hash,
            role=config.role,
            tenant=config.tenant,
            scopes=list(config.scopes),
        )
    return seeded


def authenticate_user(username: str, password: str) -> Optional[AuthUser]:
    user = get_auth_user_store().get_user(username)
    if not user:
        return None

    _validate_bcrypt_hash_or_raise(username, user.password_hash)
    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        return None

    return AuthUser(
        username=username,
        role=user.role,
        tenant=user.tenant,
        scopes=user.scopes,
    )


def _normalize_username_or_raise(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username is required.",
        )
    return normalized


def _normalize_role_or_raise(role: str) -> str:
    normalized = role.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role is required.",
        )
    return normalized


def _normalize_tenant_or_raise(tenant: str) -> str:
    normalized = tenant.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant is required.",
        )
    return normalized


def _normalize_password_or_raise(password: str) -> str:
    value = password.strip()
    if len(value) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="password must be at least 8 characters.",
        )
    return value


def list_auth_users() -> List[AuthUser]:
    users = get_auth_user_store().list_users()
    return [
        AuthUser(
            username=user.username,
            role=user.role,
            tenant=user.tenant,
            scopes=list(user.scopes),
        )
        for user in users
    ]


def get_auth_user(username: str) -> Optional[AuthUser]:
    user = get_auth_user_store().get_user(_normalize_username_or_raise(username))
    if user is None:
        return None

    return AuthUser(
        username=user.username,
        role=user.role,
        tenant=user.tenant,
        scopes=list(user.scopes),
    )


def _count_admin_users() -> int:
    return len([user for user in get_auth_user_store().list_users() if user.role == "admin"])


def create_auth_user(username: str, password: str, role: str, tenant: str, scopes: List[str]) -> AuthUser:
    normalized_username = _normalize_username_or_raise(username)
    if get_auth_user_store().get_user(normalized_username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists.",
        )

    stored = StoredAuthUser(
        username=normalized_username,
        password_hash=hash_password(_normalize_password_or_raise(password)),
        role=_normalize_role_or_raise(role),
        tenant=_normalize_tenant_or_raise(tenant),
        scopes=_normalize_scope_values(scopes),
    )
    get_auth_user_store().upsert_user(stored)

    emit_security_event(
        event_type="admin_user_created",
        outcome="allow",
        target_user=stored.username,
        role=stored.role,
        tenant=stored.tenant,
        scope=stored.scopes,
    )
    return AuthUser(
        username=stored.username,
        role=stored.role,
        tenant=stored.tenant,
        scopes=stored.scopes,
    )


def update_auth_user(
    username: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    tenant: Optional[str] = None,
    scopes: Optional[List[str]] = None,
) -> AuthUser:
    normalized_username = _normalize_username_or_raise(username)
    existing = get_auth_user_store().get_user(normalized_username)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    new_role = existing.role if role is None else _normalize_role_or_raise(role)
    if existing.role == "admin" and new_role != "admin" and _count_admin_users() <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last admin user.",
        )

    updated = StoredAuthUser(
        username=existing.username,
        password_hash=existing.password_hash if password is None else hash_password(_normalize_password_or_raise(password)),
        role=new_role,
        tenant=existing.tenant if tenant is None else _normalize_tenant_or_raise(tenant),
        scopes=existing.scopes if scopes is None else _normalize_scope_values(scopes),
    )

    get_auth_user_store().upsert_user(updated)
    emit_security_event(
        event_type="admin_user_updated",
        outcome="allow",
        target_user=updated.username,
        role=updated.role,
        tenant=updated.tenant,
        scope=updated.scopes,
    )
    return AuthUser(
        username=updated.username,
        role=updated.role,
        tenant=updated.tenant,
        scopes=updated.scopes,
    )


def delete_auth_user(username: str) -> None:
    normalized_username = _normalize_username_or_raise(username)
    existing = get_auth_user_store().get_user(normalized_username)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if existing.role == "admin" and _count_admin_users() <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the last admin user.",
        )

    get_auth_user_store().delete_user(normalized_username)
    emit_security_event(
        event_type="admin_user_deleted",
        outcome="allow",
        target_user=normalized_username,
    )


def scopes_from_claims(claims: Dict[str, Any]) -> List[str]:
    raw_scope = claims.get("scope", "")

    if isinstance(raw_scope, str):
        return _normalize_scope_values(raw_scope.split(" "))

    if isinstance(raw_scope, list):
        values: List[str] = []
        for item in raw_scope:
            if not isinstance(item, str):
                _raise_unauthorized("Invalid JWT scope.")
            values.append(item)
        return _normalize_scope_values(values)

    _raise_unauthorized("Invalid JWT scope.")


def _create_token_payload(
    subject: str,
    role: Optional[str],
    tenant: str,
    scopes: List[str],
    token_type: str,
    expires_delta: timedelta,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires_at = now + expires_delta
    payload: Dict[str, Any] = {
        "sub": subject,
        "iss": _jwt_issuer(),
        "aud": _jwt_audience(),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": token_type,
        "tenant": tenant,
        "scope": " ".join(scopes),
    }
    if role:
        payload["role"] = role
    return payload


def _encode_token(payload: Dict[str, Any]) -> str:
    keyset = _load_jwt_keyset()
    signing_key = keyset.signing_keys[keyset.active_kid]
    return jwt.encode(
        payload,
        signing_key,
        algorithm=keyset.algorithm,
        headers={"kid": keyset.active_kid},
    )


def _create_access_token_with_claims(subject: str, role: Optional[str], tenant: str, scopes: List[str]) -> Dict[str, Any]:
    payload = _create_token_payload(
        subject=subject,
        role=role,
        tenant=tenant,
        scopes=scopes,
        token_type="access",
        expires_delta=timedelta(minutes=_access_token_expires_minutes()),
    )
    return {"token": _encode_token(payload), "claims": payload}


def _create_refresh_token_with_claims(subject: str, role: Optional[str], tenant: str, scopes: List[str]) -> Dict[str, Any]:
    payload = _create_token_payload(
        subject=subject,
        role=role,
        tenant=tenant,
        scopes=scopes,
        token_type="refresh",
        expires_delta=timedelta(days=_refresh_token_expires_days()),
    )
    return {"token": _encode_token(payload), "claims": payload}


def create_token_pair(subject: str, role: Optional[str], tenant: str, scopes: List[str]) -> Dict[str, Any]:
    if not tenant:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tenant is required")

    normalized_scopes = _normalize_scope_values(scopes)

    access = _create_access_token_with_claims(subject=subject, role=role, tenant=tenant, scopes=normalized_scopes)
    refresh = _create_refresh_token_with_claims(subject=subject, role=role, tenant=tenant, scopes=normalized_scopes)

    refresh_jti = refresh["claims"]["jti"]
    refresh_exp = refresh["claims"]["exp"]
    get_security_state_backend().register_refresh_token(subject=subject, jti=refresh_jti, exp=refresh_exp)

    emit_security_event(
        event_type="token_issued",
        outcome="allow",
        subject=subject,
        tenant=tenant,
        role=role,
        scope=normalized_scopes,
        access_kid=jwt.get_unverified_header(access["token"]).get("kid"),
    )

    return {
        "access_token": access["token"],
        "refresh_token": refresh["token"],
        "token_type": "bearer",
        "access_expires_in": access_token_expires_seconds(),
        "refresh_expires_in": refresh_token_expires_seconds(),
    }


def _select_signing_key_for_token(token: str, keyset: JwtKeyset) -> str:
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError:
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="invalid_header")
        _raise_unauthorized("Invalid JWT header.")

    header_alg = header.get("alg")
    if header_alg != keyset.algorithm:
        emit_security_event(
            event_type="token_validation_failed",
            outcome="deny",
            reason="invalid_alg",
            alg=header_alg,
        )
        _raise_unauthorized("Invalid JWT algorithm.")

    kid = header.get("kid")
    if kid is None:
        if len(keyset.verification_keys) == 1:
            return next(iter(keyset.verification_keys.values()))
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="missing_kid")
        _raise_unauthorized("JWT kid is required.")

    if not isinstance(kid, str) or not kid:
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="invalid_kid")
        _raise_unauthorized("Invalid JWT kid.")

    signing_key = keyset.verification_keys.get(kid)
    if not signing_key:
        emit_security_event(
            event_type="token_validation_failed",
            outcome="deny",
            reason="unknown_kid",
            kid=kid,
        )
        _raise_unauthorized("Unknown JWT kid.")

    return signing_key


def decode_and_validate_jwt(token: str, expected_token_type: Optional[str] = "access") -> Dict[str, Any]:
    keyset = _load_jwt_keyset()
    signing_key = _select_signing_key_for_token(token, keyset)

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[keyset.algorithm],
            audience=_jwt_audience(),
            issuer=_jwt_issuer(),
            options={
                "require": ["sub", "iss", "aud", "exp", "iat", "nbf", "jti", "typ", "tenant"],
            },
        )
    except ExpiredSignatureError:
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="expired")
        _raise_unauthorized("JWT has expired.")
    except InvalidTokenError:
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="invalid_token")
        _raise_unauthorized("Invalid JWT.")

    if not isinstance(claims, dict):
        emit_security_event(event_type="token_validation_failed", outcome="deny", reason="invalid_payload")
        _raise_unauthorized("Invalid JWT payload.")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        _raise_unauthorized("Invalid JWT subject.")

    token_type = claims.get("typ")
    if not isinstance(token_type, str) or not token_type:
        _raise_unauthorized("Invalid JWT token type.")

    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        _raise_unauthorized("Invalid JWT ID.")

    role = claims.get("role")
    if role is not None and not isinstance(role, str):
        _raise_unauthorized("Invalid JWT role.")

    tenant = claims.get("tenant")
    if not isinstance(tenant, str) or not tenant:
        _raise_unauthorized("Invalid JWT tenant.")

    scopes_from_claims(claims)

    if expected_token_type and token_type != expected_token_type:
        _raise_unauthorized(f"JWT must be a {expected_token_type} token.")

    return claims


def rotate_token_pair_from_refresh_token(refresh_token: str) -> Dict[str, Any]:
    claims = decode_and_validate_jwt(token=refresh_token, expected_token_type="refresh")
    subject = claims["sub"]
    jti = claims["jti"]
    role = claims.get("role")
    tenant = claims["tenant"]
    scopes = scopes_from_claims(claims)

    consume_result = get_security_state_backend().consume_refresh_token(subject=subject, jti=jti)
    if consume_result.reused:
        emit_security_event(
            event_type="refresh_token_reuse",
            outcome="deny",
            subject=subject,
            tenant=tenant,
            jti=jti,
        )
        _raise_unauthorized("Refresh token reuse detected.")

    if not consume_result.ok:
        emit_security_event(
            event_type="refresh_token_inactive",
            outcome="deny",
            subject=subject,
            tenant=tenant,
            jti=jti,
        )
        _raise_unauthorized("Refresh token is revoked or inactive.")

    emit_security_event(
        event_type="refresh_token_rotated",
        outcome="allow",
        subject=subject,
        tenant=tenant,
        jti=jti,
    )
    return create_token_pair(subject=subject, role=role, tenant=tenant, scopes=scopes)


def resolve_client_ip(request: Request) -> str:
    if _trust_proxy_headers():
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip

    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def login_rate_limit_key(request: Request, username: str) -> str:
    normalized_username = username.strip().lower()
    client_ip = resolve_client_ip(request)
    return f"{client_ip}:{normalized_username}"


def ensure_login_not_rate_limited(key: str) -> None:
    retry_after = get_security_state_backend().check_login_rate_limit(key)
    if retry_after <= 0:
        return

    emit_security_event(
        event_type="login_rate_limited",
        outcome="deny",
        rate_key=key,
        retry_after=retry_after,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts. Please try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def register_login_failure(key: str) -> None:
    config = login_rate_limit_settings()
    get_security_state_backend().register_login_failure(
        key=key,
        max_attempts=config["max_attempts"],
        window_seconds=config["window_seconds"],
        block_seconds=config["block_seconds"],
    )
    emit_security_event(event_type="login_failed", outcome="deny", rate_key=key)


def register_login_success(key: str) -> None:
    get_security_state_backend().register_login_success(key)
    emit_security_event(event_type="login_succeeded", outcome="allow", rate_key=key)


async def require_jwt(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> JwtContext:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        emit_security_event(
            event_type="token_missing",
            outcome="deny",
            path=str(request.url.path),
            method=request.method,
        )
        _raise_unauthorized("JWT bearer token is required.")

    token = credentials.credentials
    claims = decode_and_validate_jwt(token=token, expected_token_type="access")

    jwt_context = JwtContext(token=token, claims=claims)
    request.state.jwt = jwt_context
    return jwt_context


def require_access_subject(jwt_ctx: JwtContext = Depends(require_jwt)) -> str:
    subject = jwt_ctx.claims.get("sub")
    if not isinstance(subject, str) or not subject:
        _raise_unauthorized("Invalid JWT subject.")
    return subject


def validate_auth_settings() -> None:
    keyset = _load_jwt_keyset()
    if len(keyset.signing_keys) < 2:
        emit_security_event(
            event_type="key_rotation_readiness",
            outcome="warn",
            detail="Only one signing key is configured. Keep at least two keys for safe rotation.",
        )

    _access_token_expires_minutes()
    _refresh_token_expires_days()

    seed_users = _load_auth_users()
    initialize_auth_user_store(_seed_users_for_store(seed_users))

    if get_auth_user_store().count_users() <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="At least one AUTH user must exist in the user store.",
        )

    login_rate_limit_settings()
    validate_security_state_settings()
