import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class JwtContext:
    token: str
    claims: dict[str, Any]


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("JWT must have three segments")

    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    decoded = base64.urlsafe_b64decode(payload_segment + padding)

    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JWT payload must be a JSON object")

    return payload


async def require_jwt(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> JwtContext:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT bearer token is required.",
        )

    token = credentials.credentials

    # NOTE: signature/expiration 검증은 아직 하지 않고, JWT 형식과 payload 파싱만 강제한다.
    try:
        claims = _decode_jwt_payload(token)
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT format.",
        )

    jwt_context = JwtContext(token=token, claims=claims)
    request.state.jwt = jwt_context
    return jwt_context
