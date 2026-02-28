import os
from dataclasses import dataclass
from threading import Lock
from typing import Dict, List, Optional

from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None


@dataclass
class StoredAuthUser:
    username: str
    password_hash: str
    role: str
    tenant: str
    scopes: List[str]


class AuthUserStore:
    def init_schema(self) -> None:
        raise NotImplementedError

    def count_users(self) -> int:
        raise NotImplementedError

    def get_user(self, username: str) -> Optional[StoredAuthUser]:
        raise NotImplementedError

    def list_users(self) -> List[StoredAuthUser]:
        raise NotImplementedError

    def upsert_user(self, user: StoredAuthUser) -> None:
        raise NotImplementedError

    def delete_user(self, username: str) -> bool:
        raise NotImplementedError


def _auth_user_store_backend_name() -> str:
    configured = os.getenv("AUTH_USER_STORE_BACKEND", "").strip().lower()
    if configured:
        if configured not in ("postgres", "memory"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTH_USER_STORE_BACKEND must be 'postgres' or 'memory'.",
            )
        return configured

    settings = load_database_settings()
    return "postgres" if settings.enabled else "memory"


class InMemoryAuthUserStore(AuthUserStore):
    def __init__(self) -> None:
        self._lock = Lock()
        self._users: Dict[str, StoredAuthUser] = {}

    def init_schema(self) -> None:
        return

    def count_users(self) -> int:
        with self._lock:
            return len(self._users)

    def get_user(self, username: str) -> Optional[StoredAuthUser]:
        with self._lock:
            user = self._users.get(username)
            if user is None:
                return None
            return StoredAuthUser(
                username=user.username,
                password_hash=user.password_hash,
                role=user.role,
                tenant=user.tenant,
                scopes=list(user.scopes),
            )

    def list_users(self) -> List[StoredAuthUser]:
        with self._lock:
            users = list(self._users.values())

        users.sort(key=lambda item: item.username)
        return [
            StoredAuthUser(
                username=user.username,
                password_hash=user.password_hash,
                role=user.role,
                tenant=user.tenant,
                scopes=list(user.scopes),
            )
            for user in users
        ]

    def upsert_user(self, user: StoredAuthUser) -> None:
        with self._lock:
            self._users[user.username] = StoredAuthUser(
                username=user.username,
                password_hash=user.password_hash,
                role=user.role,
                tenant=user.tenant,
                scopes=list(user.scopes),
            )

    def delete_user(self, username: str) -> bool:
        with self._lock:
            return self._users.pop(username, None) is not None


class PostgresAuthUserStore(AuthUserStore):
    def _dsn(self) -> str:
        settings = load_database_settings()
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DATABASE_ENABLED must be true when AUTH_USER_STORE_BACKEND=postgres.",
            )
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres auth user store.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for auth user store.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS auth_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            tenant TEXT NOT NULL,
            scopes TEXT[] NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def count_users(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM auth_users")
                count = int(cur.fetchone()[0])
            conn.commit()
        return count

    def get_user(self, username: str) -> Optional[StoredAuthUser]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, tenant, scopes
                    FROM auth_users
                    WHERE username = %s
                    """,
                    (username,),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            return None

        return StoredAuthUser(
            username=row[0],
            password_hash=row[1],
            role=row[2],
            tenant=row[3],
            scopes=list(row[4] or []),
        )

    def list_users(self) -> List[StoredAuthUser]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, tenant, scopes
                    FROM auth_users
                    ORDER BY username ASC
                    """
                )
                rows = cur.fetchall()
            conn.commit()

        return [
            StoredAuthUser(
                username=row[0],
                password_hash=row[1],
                role=row[2],
                tenant=row[3],
                scopes=list(row[4] or []),
            )
            for row in rows
        ]

    def upsert_user(self, user: StoredAuthUser) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_users (username, password_hash, role, tenant, scopes)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (username)
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        tenant = EXCLUDED.tenant,
                        scopes = EXCLUDED.scopes,
                        updated_at = NOW()
                    """,
                    (user.username, user.password_hash, user.role, user.tenant, user.scopes),
                )
            conn.commit()

    def delete_user(self, username: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_users WHERE username = %s", (username,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted


_memory_store = InMemoryAuthUserStore()
_postgres_store = PostgresAuthUserStore()
_store_initialized = False
_store_init_lock = Lock()


def get_auth_user_store() -> AuthUserStore:
    backend = _auth_user_store_backend_name()
    if backend == "memory":
        return _memory_store
    return _postgres_store


def initialize_auth_user_store(seed_users: Dict[str, StoredAuthUser]) -> None:
    global _store_initialized
    if _store_initialized:
        return

    with _store_init_lock:
        if _store_initialized:
            return

        store = get_auth_user_store()
        store.init_schema()

        if store.count_users() == 0:
            for user in seed_users.values():
                store.upsert_user(user)

        _store_initialized = True
