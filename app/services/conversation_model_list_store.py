import json
import os
from threading import Lock
from typing import Dict, List, Tuple

from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None


def _normalize_model_ids(model_ids: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in model_ids:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


class ConversationModelListStoreBackend:
    def init_schema(self) -> None:
        raise NotImplementedError

    def get_model_ids(self, tenant_id: str, user_id: str, conversation_id: str) -> List[str]:
        raise NotImplementedError

    def set_model_ids(self, tenant_id: str, user_id: str, conversation_id: str, model_ids: List[str]) -> List[str]:
        raise NotImplementedError


def _backend_name() -> str:
    configured = os.getenv("CONVERSATION_MODEL_LIST_BACKEND", "").strip().lower()
    if configured:
        if configured not in ("postgres", "memory"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CONVERSATION_MODEL_LIST_BACKEND must be 'postgres' or 'memory'.",
            )
        return configured

    settings = load_database_settings()
    return "postgres" if settings.enabled else "memory"


class InMemoryConversationModelListStore(ConversationModelListStoreBackend):
    def __init__(self) -> None:
        self._lock = Lock()
        self._store: Dict[Tuple[str, str, str], List[str]] = {}

    def init_schema(self) -> None:
        return

    def get_model_ids(self, tenant_id: str, user_id: str, conversation_id: str) -> List[str]:
        with self._lock:
            stored = list(self._store.get((tenant_id, user_id, conversation_id), []))
        return _normalize_model_ids(stored)

    def set_model_ids(self, tenant_id: str, user_id: str, conversation_id: str, model_ids: List[str]) -> List[str]:
        normalized = _normalize_model_ids(model_ids)
        with self._lock:
            self._store[(tenant_id, user_id, conversation_id)] = list(normalized)
        return list(normalized)


class PostgresConversationModelListStore(ConversationModelListStoreBackend):
    def _dsn(self) -> str:
        settings = load_database_settings()
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DATABASE_ENABLED must be true when CONVERSATION_MODEL_LIST_BACKEND=postgres.",
            )
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres conversation model list store.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for conversation model lists.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS conversation_model_lists (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            model_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, user_id, conversation_id)
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def get_model_ids(self, tenant_id: str, user_id: str, conversation_id: str) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_ids_json
                    FROM conversation_model_lists
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                    """,
                    (tenant_id, user_id, conversation_id),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            return []
        raw = row[0]
        if isinstance(raw, list):
            return _normalize_model_ids(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                return []
            if not isinstance(parsed, list):
                return []
            return _normalize_model_ids(parsed)
        return []

    def set_model_ids(self, tenant_id: str, user_id: str, conversation_id: str, model_ids: List[str]) -> List[str]:
        normalized = _normalize_model_ids(model_ids)
        payload = json.dumps(normalized)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_model_lists (tenant_id, user_id, conversation_id, model_ids_json)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (tenant_id, user_id, conversation_id)
                    DO UPDATE SET
                        model_ids_json = EXCLUDED.model_ids_json,
                        updated_at = NOW()
                    """,
                    (tenant_id, user_id, conversation_id, payload),
                )
            conn.commit()
        return normalized


_memory_store = InMemoryConversationModelListStore()
_postgres_store = PostgresConversationModelListStore()
_store_initialized = False
_store_init_lock = Lock()


def _get_store() -> ConversationModelListStoreBackend:
    if _backend_name() == "memory":
        return _memory_store
    return _postgres_store


def initialize_conversation_model_list_store() -> None:
    global _store_initialized
    if _store_initialized:
        return

    with _store_init_lock:
        if _store_initialized:
            return
        store = _get_store()
        store.init_schema()
        _store_initialized = True


class ConversationModelListStore:
    def get_model_ids(self, tenant_id: str, user_id: str, conversation_id: str) -> List[str]:
        initialize_conversation_model_list_store()
        return _get_store().get_model_ids(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def set_model_ids(self, tenant_id: str, user_id: str, conversation_id: str, model_ids: List[str]) -> List[str]:
        initialize_conversation_model_list_store()
        return _get_store().set_model_ids(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            model_ids=model_ids,
        )


conversation_model_list_store = ConversationModelListStore()
