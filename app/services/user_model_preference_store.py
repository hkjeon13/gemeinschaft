import os
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None


class UserModelPreferenceStoreBackend:
    def init_schema(self) -> None:
        raise NotImplementedError

    def get_default_model_id(self, tenant_id: str, user_id: str) -> Optional[str]:
        raise NotImplementedError

    def set_default_model_id(self, tenant_id: str, user_id: str, model_id: str) -> None:
        raise NotImplementedError

    def clear_default_model_id(self, tenant_id: str, user_id: str) -> None:
        raise NotImplementedError


def _backend_name() -> str:
    configured = os.getenv("USER_MODEL_PREFERENCE_BACKEND", "").strip().lower()
    if configured:
        if configured not in ("postgres", "memory"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="USER_MODEL_PREFERENCE_BACKEND must be 'postgres' or 'memory'.",
            )
        return configured

    settings = load_database_settings()
    return "postgres" if settings.enabled else "memory"


class InMemoryUserModelPreferenceStore(UserModelPreferenceStoreBackend):
    def __init__(self) -> None:
        self._lock = Lock()
        self._store: Dict[Tuple[str, str], str] = {}

    def init_schema(self) -> None:
        return

    def get_default_model_id(self, tenant_id: str, user_id: str) -> Optional[str]:
        with self._lock:
            value = self._store.get((tenant_id, user_id))
        return str(value).strip() if value else None

    def set_default_model_id(self, tenant_id: str, user_id: str, model_id: str) -> None:
        normalized = model_id.strip()
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_id is required.")
        with self._lock:
            self._store[(tenant_id, user_id)] = normalized

    def clear_default_model_id(self, tenant_id: str, user_id: str) -> None:
        with self._lock:
            self._store.pop((tenant_id, user_id), None)


class PostgresUserModelPreferenceStore(UserModelPreferenceStoreBackend):
    def _dsn(self) -> str:
        settings = load_database_settings()
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DATABASE_ENABLED must be true when USER_MODEL_PREFERENCE_BACKEND=postgres.",
            )
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres user model preference store.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for user model preferences.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS user_model_preferences (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            default_model_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, user_id)
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def get_default_model_id(self, tenant_id: str, user_id: str) -> Optional[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT default_model_id
                    FROM user_model_preferences
                    WHERE tenant_id = %s AND user_id = %s
                    """,
                    (tenant_id, user_id),
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            return None
        value = str(row[0] or "").strip()
        return value or None

    def set_default_model_id(self, tenant_id: str, user_id: str, model_id: str) -> None:
        normalized = model_id.strip()
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_id is required.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_model_preferences (tenant_id, user_id, default_model_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (tenant_id, user_id)
                    DO UPDATE SET
                        default_model_id = EXCLUDED.default_model_id,
                        updated_at = NOW()
                    """,
                    (tenant_id, user_id, normalized),
                )
            conn.commit()

    def clear_default_model_id(self, tenant_id: str, user_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM user_model_preferences
                    WHERE tenant_id = %s AND user_id = %s
                    """,
                    (tenant_id, user_id),
                )
            conn.commit()


_memory_store = InMemoryUserModelPreferenceStore()
_postgres_store = PostgresUserModelPreferenceStore()
_store_initialized = False
_store_init_lock = Lock()


def _get_store() -> UserModelPreferenceStoreBackend:
    if _backend_name() == "memory":
        return _memory_store
    return _postgres_store


def initialize_user_model_preference_store() -> None:
    global _store_initialized
    if _store_initialized:
        return

    with _store_init_lock:
        if _store_initialized:
            return
        store = _get_store()
        store.init_schema()
        _store_initialized = True


class UserModelPreferenceStore:
    def get_default_model_id(self, tenant_id: str, user_id: str) -> Optional[str]:
        initialize_user_model_preference_store()
        return _get_store().get_default_model_id(tenant_id=tenant_id, user_id=user_id)

    def set_default_model_id(self, tenant_id: str, user_id: str, model_id: str) -> None:
        initialize_user_model_preference_store()
        _get_store().set_default_model_id(tenant_id=tenant_id, user_id=user_id, model_id=model_id)

    def clear_default_model_id(self, tenant_id: str, user_id: str) -> None:
        initialize_user_model_preference_store()
        _get_store().clear_default_model_id(tenant_id=tenant_id, user_id=user_id)


user_model_preference_store = UserModelPreferenceStore()
