import os
from dataclasses import dataclass
from datetime import datetime
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
    name: str = ""
    email: Optional[str] = None
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None
    email_verification_token_hash: Optional[str] = None
    email_verification_expires_at: Optional[datetime] = None


class AuthUserStore:
    def init_schema(self) -> None:
        raise NotImplementedError

    def count_users(self) -> int:
        raise NotImplementedError

    def get_user(self, username: str) -> Optional[StoredAuthUser]:
        raise NotImplementedError

    def get_user_by_email(self, email: str) -> Optional[StoredAuthUser]:
        raise NotImplementedError

    def get_user_by_email_verification_token_hash(self, token_hash: str) -> Optional[StoredAuthUser]:
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
                name=user.name,
                email=user.email,
                email_verified=user.email_verified,
                email_verified_at=user.email_verified_at,
                email_verification_token_hash=user.email_verification_token_hash,
                email_verification_expires_at=user.email_verification_expires_at,
            )

    def get_user_by_email(self, email: str) -> Optional[StoredAuthUser]:
        normalized_email = email.strip().lower()
        if not normalized_email:
            return None
        with self._lock:
            for user in self._users.values():
                if (user.email or "").lower() == normalized_email:
                    return StoredAuthUser(
                        username=user.username,
                        password_hash=user.password_hash,
                        role=user.role,
                        tenant=user.tenant,
                        scopes=list(user.scopes),
                        name=user.name,
                        email=user.email,
                        email_verified=user.email_verified,
                        email_verified_at=user.email_verified_at,
                        email_verification_token_hash=user.email_verification_token_hash,
                        email_verification_expires_at=user.email_verification_expires_at,
                    )
        return None

    def get_user_by_email_verification_token_hash(self, token_hash: str) -> Optional[StoredAuthUser]:
        normalized_hash = token_hash.strip()
        if not normalized_hash:
            return None
        with self._lock:
            for user in self._users.values():
                if user.email_verification_token_hash == normalized_hash:
                    return StoredAuthUser(
                        username=user.username,
                        password_hash=user.password_hash,
                        role=user.role,
                        tenant=user.tenant,
                        scopes=list(user.scopes),
                        name=user.name,
                        email=user.email,
                        email_verified=user.email_verified,
                        email_verified_at=user.email_verified_at,
                        email_verification_token_hash=user.email_verification_token_hash,
                        email_verification_expires_at=user.email_verification_expires_at,
                    )
        return None

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
                name=user.name,
                email=user.email,
                email_verified=user.email_verified,
                email_verified_at=user.email_verified_at,
                email_verification_token_hash=user.email_verification_token_hash,
                email_verification_expires_at=user.email_verification_expires_at,
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
                name=user.name,
                email=user.email,
                email_verified=user.email_verified,
                email_verified_at=user.email_verified_at,
                email_verification_token_hash=user.email_verification_token_hash,
                email_verification_expires_at=user.email_verification_expires_at,
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
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS auth_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            tenant TEXT NOT NULL,
            scopes TEXT[] NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            email TEXT,
            email_verified BOOLEAN NOT NULL DEFAULT FALSE,
            email_verified_at TIMESTAMPTZ,
            email_verification_token_hash TEXT,
            email_verification_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        migration_sql = """
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email TEXT;
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email_verification_token_hash TEXT;
        ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email_verification_expires_at TIMESTAMPTZ;
        """
        index_sql = """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_email_unique
            ON auth_users (email)
            WHERE email IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_auth_users_email_verification_token_hash
            ON auth_users (email_verification_token_hash)
            WHERE email_verification_token_hash IS NOT NULL;
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
                cur.execute(migration_sql)
                cur.execute(index_sql)
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
                    SELECT username, password_hash, role, tenant, scopes, name, email,
                           email_verified, email_verified_at, email_verification_token_hash, email_verification_expires_at
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
            name=row[5] or "",
            email=row[6],
            email_verified=bool(row[7]),
            email_verified_at=row[8],
            email_verification_token_hash=row[9],
            email_verification_expires_at=row[10],
        )

    def get_user_by_email(self, email: str) -> Optional[StoredAuthUser]:
        normalized_email = email.strip().lower()
        if not normalized_email:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, tenant, scopes, name, email,
                           email_verified, email_verified_at, email_verification_token_hash, email_verification_expires_at
                    FROM auth_users
                    WHERE email = %s
                    """,
                    (normalized_email,),
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
            name=row[5] or "",
            email=row[6],
            email_verified=bool(row[7]),
            email_verified_at=row[8],
            email_verification_token_hash=row[9],
            email_verification_expires_at=row[10],
        )

    def get_user_by_email_verification_token_hash(self, token_hash: str) -> Optional[StoredAuthUser]:
        normalized_hash = token_hash.strip()
        if not normalized_hash:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, tenant, scopes, name, email,
                           email_verified, email_verified_at, email_verification_token_hash, email_verification_expires_at
                    FROM auth_users
                    WHERE email_verification_token_hash = %s
                    """,
                    (normalized_hash,),
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
            name=row[5] or "",
            email=row[6],
            email_verified=bool(row[7]),
            email_verified_at=row[8],
            email_verification_token_hash=row[9],
            email_verification_expires_at=row[10],
        )

    def list_users(self) -> List[StoredAuthUser]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, tenant, scopes, name, email,
                           email_verified, email_verified_at, email_verification_token_hash, email_verification_expires_at
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
                name=row[5] or "",
                email=row[6],
                email_verified=bool(row[7]),
                email_verified_at=row[8],
                email_verification_token_hash=row[9],
                email_verification_expires_at=row[10],
            )
            for row in rows
        ]

    def upsert_user(self, user: StoredAuthUser) -> None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO auth_users (
                            username, password_hash, role, tenant, scopes,
                            name, email, email_verified, email_verified_at,
                            email_verification_token_hash, email_verification_expires_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username)
                        DO UPDATE SET
                            password_hash = EXCLUDED.password_hash,
                            role = EXCLUDED.role,
                            tenant = EXCLUDED.tenant,
                            scopes = EXCLUDED.scopes,
                            name = EXCLUDED.name,
                            email = EXCLUDED.email,
                            email_verified = EXCLUDED.email_verified,
                            email_verified_at = EXCLUDED.email_verified_at,
                            email_verification_token_hash = EXCLUDED.email_verification_token_hash,
                            email_verification_expires_at = EXCLUDED.email_verification_expires_at,
                            updated_at = NOW()
                        """,
                        (
                            user.username,
                            user.password_hash,
                            user.role,
                            user.tenant,
                            user.scopes,
                            user.name,
                            user.email,
                            user.email_verified,
                            user.email_verified_at,
                            user.email_verification_token_hash,
                            user.email_verification_expires_at,
                        ),
                    )
                conn.commit()
        except Exception as exc:
            if getattr(exc, "sqlstate", "") == "23505":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already exists.",
                )
            raise

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
