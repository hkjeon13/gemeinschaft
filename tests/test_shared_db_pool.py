"""Tests for shared DB connection helpers with optional pooling."""

from __future__ import annotations

from typing import Any

import pytest

from services.shared import db


class _DummyDirectConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakePsycopgModule:
    def __init__(self) -> None:
        self.connect_calls: list[str] = []

    def connect(self, dsn: str) -> _DummyDirectConnection:
        self.connect_calls.append(dsn)
        return _DummyDirectConnection()


class _FakePool:
    instances: list["_FakePool"] = []

    def __init__(
        self,
        *,
        conninfo: str,
        min_size: int,
        max_size: int,
        timeout: float,
        open: bool,
    ) -> None:
        self.conninfo = conninfo
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.open = open
        self.getconn_calls = 0
        self.putconn_calls = 0
        self.close_calls = 0
        self.last_getconn_timeout: float | None = None
        _FakePool.instances.append(self)

    def getconn(self, timeout: float | None = None) -> Any:
        self.getconn_calls += 1
        self.last_getconn_timeout = timeout
        return {"id": self.getconn_calls}

    def putconn(self, connection: Any) -> None:
        del connection
        self.putconn_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class _FakePoolModule:
    ConnectionPool = _FakePool


def test_get_db_connection_uses_pool_when_available(monkeypatch: Any) -> None:
    db.close_all_db_pools()
    _FakePool.instances.clear()
    fake_psycopg = _FakePsycopgModule()

    def _fake_import(name: str) -> Any:
        if name == "psycopg_pool":
            return _FakePoolModule
        if name == "psycopg":
            return fake_psycopg
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(db, "_import_module", _fake_import)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("DB_POOL_ENABLED", "true")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "4.5")

    connection = db.get_db_connection("scheduler")

    assert len(_FakePool.instances) == 1
    pool = _FakePool.instances[0]
    assert pool.conninfo == "postgresql://local/test"
    assert pool.min_size == 1
    assert pool.max_size == 3
    assert pool.timeout == 4.5
    assert pool.getconn_calls == 1
    assert pool.last_getconn_timeout == 4.5
    connection.close()
    assert pool.putconn_calls == 1
    assert fake_psycopg.connect_calls == []

    db.close_all_db_pools()
    assert pool.close_calls == 1


def test_get_db_connection_falls_back_to_direct_when_pool_module_missing(
    monkeypatch: Any,
) -> None:
    db.close_all_db_pools()
    fake_psycopg = _FakePsycopgModule()

    def _fake_import(name: str) -> Any:
        if name == "psycopg_pool":
            raise ModuleNotFoundError(name)
        if name == "psycopg":
            return fake_psycopg
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(db, "_import_module", _fake_import)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("DB_POOL_ENABLED", "true")

    connection = db.get_db_connection("scheduler")

    assert isinstance(connection, _DummyDirectConnection)
    assert fake_psycopg.connect_calls == ["postgresql://local/test"]


def test_get_db_connection_uses_direct_when_pool_disabled(monkeypatch: Any) -> None:
    db.close_all_db_pools()
    _FakePool.instances.clear()
    fake_psycopg = _FakePsycopgModule()

    def _fake_import(name: str) -> Any:
        if name == "psycopg_pool":
            return _FakePoolModule
        if name == "psycopg":
            return fake_psycopg
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(db, "_import_module", _fake_import)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("DB_POOL_ENABLED", "false")

    connection = db.get_db_connection("scheduler")

    assert isinstance(connection, _DummyDirectConnection)
    assert fake_psycopg.connect_calls == ["postgresql://local/test"]
    assert _FakePool.instances == []


def test_get_db_connection_rejects_invalid_pool_settings(monkeypatch: Any) -> None:
    db.close_all_db_pools()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("DB_POOL_ENABLED", "true")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "5")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")

    with pytest.raises(RuntimeError, match="DB_POOL_MAX_SIZE must be >= DB_POOL_MIN_SIZE"):
        db.get_db_connection("scheduler")


def test_get_db_connection_can_require_pool_module(monkeypatch: Any) -> None:
    db.close_all_db_pools()
    fake_psycopg = _FakePsycopgModule()

    def _fake_import(name: str) -> Any:
        if name == "psycopg_pool":
            raise ModuleNotFoundError(name)
        if name == "psycopg":
            return fake_psycopg
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(db, "_import_module", _fake_import)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setenv("DB_POOL_ENABLED", "true")
    monkeypatch.setenv("DB_POOL_REQUIRE", "true")

    with pytest.raises(RuntimeError, match="psycopg_pool is not installed"):
        db.get_db_connection("scheduler")
