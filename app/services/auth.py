import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError

from .security_state import login_rate_limit_settings, login_rate_limiter, refresh_token_registry

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class JwtContext:
    token: str
    claims: Dict[str, Any]


@dataclass
class AuthUser:
    username: str
    role: str


@dataclass
class AuthUserRecord:
    password_hash: Optional[str]
    plain_password: Optional[str]
    role: str


def _raise_unauthorized(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _jwt_secret_key() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication is not configured. Set JWT_SECRET_KEY.",
        )
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET_KEY must be at least 32 characters.",
        )
    return secret


def _jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _jwt_issuer() -> str:
    return os.getenv("JWT_ISSUER", "gemeinschaft-api")


def _jwt_audience() -> str:
    return os.getenv("JWT_AUDIENCE", "gemeinschaft-clients")


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
            detail=f"AUTH_USERS_JSON user '{username}' has invalid bcrypt hash.",
        )


def _load_auth_users() -> Dict[str, AuthUserRecord]:
    raw_users = os.getenv("AUTH_USERS_JSON", "").strip()
    if not raw_users:
        return {}

    try:
        parsed = json.loads(raw_users)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_USERS_JSON must be valid JSON.",
        )

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_USERS_JSON must be a JSON object.",
        )

    users: Dict[str, AuthUserRecord] = {}
    for username, config in parsed.items():
        if not isinstance(username, str) or not username:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTH_USERS_JSON keys must be non-empty usernames.",
            )

        role = "user"
        password_hash: Optional[str] = None
        plain_password: Optional[str] = None

        if isinstance(config, str):
            password_hash = config
        elif isinstance(config, dict):
            role = config.get("role", "user")
            password_hash = config.get("password_hash")
            plain_password = config.get("password")
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH_USERS_JSON user '{username}' has invalid config.",
            )

        if not isinstance(role, str) or not role:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AUTH_USERS_JSON user '{username}' role must be a non-empty string.",
            )

        if password_hash is not None:
            if not isinstance(password_hash, str) or not password_hash:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTH_USERS_JSON user '{username}' password_hash must be a non-empty string.",
                )
            _validate_bcrypt_hash_or_raise(username, password_hash)
            users[username] = AuthUserRecord(password_hash=password_hash, plain_password=None, role=role)
            continue

        if plain_password is not None:
            if not isinstance(plain_password, str) or not plain_password:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"AUTH_USERS_JSON user '{username}' password must be a non-empty string.",
                )
            if not _allow_plaintext_passwords():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"AUTH_USERS_JSON user '{username}' uses plaintext password. "
                        "Use password_hash (bcrypt) or set AUTH_ALLOW_PLAINTEXT_PASSWORDS=true only for development."
                    ),
                )
            users[username] = AuthUserRecord(password_hash=None, plain_password=plain_password, role=role)
            continue

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AUTH_USERS_JSON user '{username}' must define password_hash.",
        )

    return users


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, record: AuthUserRecord) -> bool:
    if record.password_hash is not None:
        return bcrypt.checkpw(plain_password.encode("utf-8"), record.password_hash.encode("utf-8"))

    if record.plain_password is not None:
        return secrets.compare_digest(record.plain_password, plain_password)

    return False


def authenticate_user(username: str, password: str) -> Optional[AuthUser]:
    users = _load_auth_users()
    user = users.get(username)
    if not user:
        return None

    if not verify_password(password, user):
        return None

    return AuthUser(username=username, role=user.role)


def _create_token_payload(subject: str, role: Optional[str], token_type: str, expires_delta: timedelta) -> Dict[str, Any]:
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
    }
    if role:
        payload["role"] = role
    return payload


def _encode_token(payload: Dict[str, Any]) -> str:
    return jwt.encode(payload, _jwt_secret_key(), algorithm=_jwt_algorithm())


def _create_access_token_with_claims(subject: str, role: Optional[str]) -> Dict[str, Any]:
    payload = _create_token_payload(
        subject=subject,
        role=role,
        token_type="access",
        expires_delta=timedelta(minutes=_access_token_expires_minutes()),
    )
    return {"token": _encode_token(payload), "claims": payload}


def _create_refresh_token_with_claims(subject: str, role: Optional[str]) -> Dict[str, Any]:
    payload = _create_token_payload(
        subject=subject,
        role=role,
        token_type="refresh",
        expires_delta=timedelta(days=_refresh_token_expires_days()),
    )
    return {"token": _encode_token(payload), "claims": payload}


def create_token_pair(subject: str, role: Optional[str] = None) -> Dict[str, Any]:
    access = _create_access_token_with_claims(subject=subject, role=role)
    refresh = _create_refresh_token_with_claims(subject=subject, role=role)

    refresh_jti = refresh["claims"]["jti"]
    refresh_exp = refresh["claims"]["exp"]
    refresh_token_registry.register(subject=subject, jti=refresh_jti, exp=refresh_exp)

    return {
        "access_token": access["token"],
        "refresh_token": refresh["token"],
        "token_type": "bearer",
        "access_expires_in": access_token_expires_seconds(),
        "refresh_expires_in": refresh_token_expires_seconds(),
    }


def decode_and_validate_jwt(token: str, expected_token_type: Optional[str] = "access") -> Dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            _jwt_secret_key(),
            algorithms=[_jwt_algorithm()],
            audience=_jwt_audience(),
            issuer=_jwt_issuer(),
            options={
                "require": ["sub", "iss", "aud", "exp", "iat", "nbf", "jti", "typ"],
            },
        )
    except ExpiredSignatureError:
        _raise_unauthorized("JWT has expired.")
    except InvalidTokenError:
        _raise_unauthorized("Invalid JWT.")

    if not isinstance(claims, dict):
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

    if expected_token_type and token_type != expected_token_type:
        _raise_unauthorized(f"JWT must be a {expected_token_type} token.")

    return claims


def rotate_token_pair_from_refresh_token(refresh_token: str) -> Dict[str, Any]:
    claims = decode_and_validate_jwt(token=refresh_token, expected_token_type="refresh")
    subject = claims["sub"]
    jti = claims["jti"]
    role = claims.get("role")

    consume_result = refresh_token_registry.consume(subject=subject, jti=jti)
    if consume_result.reused:
        _raise_unauthorized("Refresh token reuse detected.")
    if not consume_result.ok:
        _raise_unauthorized("Refresh token is revoked or inactive.")

    return create_token_pair(subject=subject, role=role)


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
    retry_after = login_rate_limiter.check(key)
    if retry_after <= 0:
        return

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts. Please try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def register_login_failure(key: str) -> None:
    login_rate_limiter.register_failure(key)


def register_login_success(key: str) -> None:
    login_rate_limiter.register_success(key)


async def require_jwt(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> JwtContext:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
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
    _jwt_secret_key()
    _access_token_expires_minutes()
    _refresh_token_expires_days()

    users = _load_auth_users()
    if not users:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_USERS_JSON must include at least one user.",
        )

    login_rate_limit_settings()
