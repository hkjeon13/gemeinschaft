"""Shared database connection helpers with optional pooling."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class _PoolSettings:
    min_size: int
    max_size: int
    timeout_seconds: float


class _PooledConnection:
    def __init__(self, *, pool: Any, connection: Any):
        self._pool = pool
        self._connection = connection
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        putconn = getattr(self._pool, "putconn", None)
        if callable(putconn):
            putconn(self._connection)
            return
        self._connection.close()

    def __enter__(self) -> "_PooledConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


_POOL_LOCK = Lock()
_POOLS: dict[tuple[str, str, int, int, float], Any] = {}


def _import_module(name: str) -> Any:
    return importlib.import_module(name)


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _parse_float_env(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _load_settings() -> _PoolSettings:
    min_size = _parse_int_env("DB_POOL_MIN_SIZE", 1, minimum=1)
    max_size = _parse_int_env("DB_POOL_MAX_SIZE", 10, minimum=1)
    if max_size < min_size:
        raise RuntimeError("DB_POOL_MAX_SIZE must be >= DB_POOL_MIN_SIZE")
    timeout_seconds = _parse_float_env("DB_POOL_TIMEOUT_SECONDS", 10.0, minimum=0.1)
    return _PoolSettings(
        min_size=min_size,
        max_size=max_size,
        timeout_seconds=timeout_seconds,
    )


def _connect_direct(database_url: str) -> Any:
    try:
        psycopg = _import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is not installed") from exc
    return psycopg.connect(database_url)


def _build_pool(database_url: str, settings: _PoolSettings) -> Any | None:
    try:
        psycopg_pool = _import_module("psycopg_pool")
    except ModuleNotFoundError:
        return None
    return psycopg_pool.ConnectionPool(
        conninfo=database_url,
        min_size=settings.min_size,
        max_size=settings.max_size,
        timeout=settings.timeout_seconds,
        open=True,
    )


def get_db_connection(service_name: str) -> Any:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    if not _parse_bool_env("DB_POOL_ENABLED", default=True):
        return _connect_direct(database_url)

    settings = _load_settings()
    pool_key = (
        service_name,
        database_url,
        settings.min_size,
        settings.max_size,
        settings.timeout_seconds,
    )
    with _POOL_LOCK:
        pool = _POOLS.get(pool_key)
        if pool is None:
            pool = _build_pool(database_url, settings)
            if pool is not None:
                _POOLS[pool_key] = pool
    if pool is None:
        if _parse_bool_env("DB_POOL_REQUIRE", default=False):
            raise RuntimeError("psycopg_pool is not installed")
        return _connect_direct(database_url)
    try:
        raw_connection = pool.getconn(timeout=settings.timeout_seconds)
    except TypeError:
        raw_connection = pool.getconn()
    return _PooledConnection(pool=pool, connection=raw_connection)


def close_all_db_pools() -> None:
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        close_fn = getattr(pool, "close", None)
        if callable(close_fn):
            close_fn()
