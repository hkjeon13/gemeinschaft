import os
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status


@dataclass
class DatabaseSettings:
    enabled: bool
    host: str
    port: int
    db_name: str
    user: str
    password: str
    url: Optional[str]


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y")


def _db_enabled() -> bool:
    return _truthy(os.getenv("DATABASE_ENABLED", "false"))


def _parse_port(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_PORT must be an integer.",
        )
    if value <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_PORT must be greater than 0.",
        )
    return value


def load_database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        enabled=_db_enabled(),
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=_parse_port(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "gemeinschaft"),
        user=os.getenv("POSTGRES_USER", "gemeinschaft"),
        password=os.getenv("POSTGRES_PASSWORD", "gemeinschaft"),
        url=os.getenv("DATABASE_URL"),
    )


def database_url_from_settings(settings: DatabaseSettings) -> str:
    if settings.url:
        return settings.url

    return (
        f"postgresql://{settings.user}:{settings.password}"
        f"@{settings.host}:{settings.port}/{settings.db_name}"
    )


def validate_database_settings() -> None:
    settings = load_database_settings()
    if not settings.enabled:
        return

    if not settings.host:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_HOST is required when DATABASE_ENABLED=true.",
        )

    if not settings.db_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_DB is required when DATABASE_ENABLED=true.",
        )

    if not settings.user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_USER is required when DATABASE_ENABLED=true.",
        )

    if not settings.password and not settings.url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="POSTGRES_PASSWORD is required unless DATABASE_URL is set.",
        )

    # Connection/schema는 아직 준비 단계라 실제 DB 연결은 하지 않는다.
    database_url_from_settings(settings)
