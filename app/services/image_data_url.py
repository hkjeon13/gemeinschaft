import base64
import binascii
import re
from typing import Optional, Set

from fastapi import HTTPException, status

_DATA_URL_PATTERN = re.compile(
    r"^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)
_DEFAULT_ALLOWED_IMAGE_MIME_TYPES: Set[str] = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}


def normalize_image_data_url_or_raise(
    *,
    field_name: str,
    value: str,
    max_bytes: int,
    allowed_mime_types: Optional[Set[str]] = None,
) -> str:
    raw = value.strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required.",
        )

    matched = _DATA_URL_PATTERN.fullmatch(raw)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a valid data:image/*;base64 URL.",
        )

    mime_type = matched.group(1).lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"

    allowed = allowed_mime_types or _DEFAULT_ALLOWED_IMAGE_MIME_TYPES
    if mime_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} has unsupported image type.",
        )

    encoded = "".join(matched.group(2).split())
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} base64 payload is invalid.",
        )

    if not decoded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} image payload is empty.",
        )

    if len(decoded) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} exceeds size limit ({max_bytes} bytes).",
        )

    canonical_encoded = base64.b64encode(decoded).decode("ascii")
    return f"data:{mime_type};base64,{canonical_encoded}"
