import hashlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError
from jwt.algorithms import ECAlgorithm

from .security_audit import emit_security_event

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_DPoP_REPLAY_CACHE: Dict[str, int] = {}
_DPoP_REPLAY_LOCK = threading.Lock()


def _env_truthy(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in ("1", "true", "yes", "y")


def auth_require_csrf() -> bool:
    return _env_truthy("AUTH_REQUIRE_CSRF", "true")


def auth_require_dpop() -> bool:
    return _env_truthy("AUTH_REQUIRE_DPOP", "true")


def csrf_cookie_name() -> str:
    name = os.getenv("AUTH_CSRF_COOKIE_NAME", "csrf_token").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_CSRF_COOKIE_NAME must be a non-empty string.",
        )
    return name


def csrf_header_name() -> str:
    return "x-csrf-token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("AUTH_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return []
    return [origin.strip().lower().rstrip("/") for origin in raw.split(",") if origin.strip()]


def _origin_regex_pattern() -> str:
    return os.getenv("AUTH_ALLOWED_ORIGIN_REGEX", "").strip()


def _origin_matches_allowed_regex(origin: str, pattern: str) -> bool:
    try:
        return re.fullmatch(pattern, origin, flags=re.IGNORECASE) is not None
    except re.error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_ALLOWED_ORIGIN_REGEX must be a valid regular expression.",
        )


def _request_external_origin(request: Request) -> str:
    host = request.headers.get("host", "").strip()
    if not host:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Host header is required.",
        )

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded_proto in ("http", "https"):
        scheme = forwarded_proto
    else:
        scheme = request.url.scheme

    return f"{scheme}://{host}".lower().rstrip("/")


def enforce_origin_for_state_change(request: Request) -> None:
    if request.method.upper() in _SAFE_METHODS:
        return

    origin = request.headers.get("origin", "").strip().lower().rstrip("/")
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin header is required for state-changing requests.",
        )

    allowed = _parse_allowed_origins()
    allowed_regex = _origin_regex_pattern()
    if not allowed:
        if not allowed_regex:
            allowed = [_request_external_origin(request)]

    if "*" in allowed:
        return

    if origin in allowed:
        return

    if allowed_regex and _origin_matches_allowed_regex(origin, allowed_regex):
        return

    if origin not in allowed:
        emit_security_event(
            event_type="origin_denied",
            outcome="deny",
            origin=origin,
            allowed_origins=allowed,
            allowed_origin_regex=allowed_regex or None,
            path=request.url.path,
            method=request.method,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin is not allowed.",
        )


def enforce_csrf_for_state_change(request: Request) -> None:
    if not auth_require_csrf():
        return

    if request.method.upper() in _SAFE_METHODS:
        return

    cookie_name = csrf_cookie_name()
    cookie_value = request.cookies.get(cookie_name, "")
    header_value = request.headers.get(csrf_header_name(), "")

    if not cookie_value or not header_value or not secrets.compare_digest(cookie_value, header_value):
        emit_security_event(
            event_type="csrf_denied",
            outcome="deny",
            path=request.url.path,
            method=request.method,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
        )


def _to_base64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def dpop_jkt_from_jwk(jwk: Dict[str, Any]) -> str:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP key must be EC P-256.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    for field in ("x", "y"):
        value = jwk.get(field)
        if not isinstance(value, str) or not value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid DPoP JWK.",
                headers={"WWW-Authenticate": "DPoP"},
            )

    canonical = json.dumps(
        {
            "crv": jwk["crv"],
            "kty": jwk["kty"],
            "x": jwk["x"],
            "y": jwk["y"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _to_base64url(hashlib.sha256(canonical).digest())


def _validate_dpop_replay(jkt: str, jti: str, iat: int, tolerance: int) -> None:
    now = int(time.time())
    key = hashlib.sha256(f"{jkt}:{jti}:{iat}".encode("utf-8")).hexdigest()
    expires_at = now + tolerance
    with _DPoP_REPLAY_LOCK:
        expired = [cache_key for cache_key, cache_exp in _DPoP_REPLAY_CACHE.items() if cache_exp <= now]
        for cache_key in expired:
            _DPoP_REPLAY_CACHE.pop(cache_key, None)

        if key in _DPoP_REPLAY_CACHE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="DPoP replay detected.",
                headers={"WWW-Authenticate": "DPoP"},
            )

        _DPoP_REPLAY_CACHE[key] = expires_at


def _validate_dpop_htu(request: Request, htu: str) -> None:
    parsed = urlparse(htu)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid DPoP htu.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    proof_host = parsed.netloc.lower()
    request_host = request.headers.get("host", "").strip().lower()
    if not request_host or proof_host != request_host:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP htu host mismatch.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    request_path = request.url.path
    valid_paths = {request_path, f"/api{request_path}"}
    if parsed.path not in valid_paths:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP htu path mismatch.",
            headers={"WWW-Authenticate": "DPoP"},
        )


def validate_dpop_proof(request: Request, expected_jkt: Optional[str] = None) -> Optional[str]:
    if not auth_require_dpop():
        return None

    proof = request.headers.get("dpop", "").strip()
    if not proof:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP proof is required.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    try:
        header = jwt.get_unverified_header(proof)
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid DPoP header.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    if header.get("alg") != "ES256" or header.get("typ") != "dpop+jwt":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP alg/type is invalid.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    jwk = header.get("jwk")
    if not isinstance(jwk, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP JWK is required.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    try:
        public_key = ECAlgorithm.from_jwk(json.dumps(jwk))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid DPoP JWK.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    try:
        claims = jwt.decode(
            proof,
            public_key,
            algorithms=["ES256"],
            options={"require": ["htu", "htm", "iat", "jti"], "verify_aud": False, "verify_iss": False},
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid DPoP proof.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    htm = claims.get("htm")
    htu = claims.get("htu")
    iat = claims.get("iat")
    jti = claims.get("jti")
    if not isinstance(htm, str) or not isinstance(htu, str) or not isinstance(iat, int) or not isinstance(jti, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid DPoP payload.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    if htm.upper() != request.method.upper():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP htm mismatch.",
            headers={"WWW-Authenticate": "DPoP"},
        )
    _validate_dpop_htu(request, htu)

    tolerance_raw = os.getenv("DPOP_IAT_TOLERANCE_SECONDS", "300")
    try:
        tolerance = int(tolerance_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DPOP_IAT_TOLERANCE_SECONDS must be an integer.",
        )
    if tolerance <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DPOP_IAT_TOLERANCE_SECONDS must be greater than 0.",
        )

    now = int(time.time())
    if abs(now - iat) > tolerance:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP iat is outside allowed window.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    jkt = dpop_jkt_from_jwk(jwk)
    if expected_jkt and expected_jkt != jkt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DPoP key mismatch.",
            headers={"WWW-Authenticate": "DPoP"},
        )

    _validate_dpop_replay(jkt=jkt, jti=jti, iat=iat, tolerance=tolerance)
    return jkt
