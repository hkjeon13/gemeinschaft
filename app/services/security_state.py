import math
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, Dict, Optional

from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None


@dataclass
class RefreshConsumeResult:
    ok: bool
    reused: bool


class SecurityStateBackend:
    def init_schema(self) -> None:
        raise NotImplementedError

    def register_refresh_token(self, subject: str, jti: str, exp: int) -> None:
        raise NotImplementedError

    def consume_refresh_token(self, subject: str, jti: str) -> RefreshConsumeResult:
        raise NotImplementedError

    def check_login_rate_limit(self, key: str) -> int:
        raise NotImplementedError

    def register_login_failure(self, key: str, max_attempts: int, window_seconds: int, block_seconds: int) -> None:
        raise NotImplementedError

    def register_login_success(self, key: str) -> None:
        raise NotImplementedError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_from_epoch(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


def _parse_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be an integer.",
        )
    if value <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be greater than 0.",
        )
    return value


def _security_backend_name() -> str:
    backend = os.getenv("SECURITY_STATE_BACKEND", "postgres").strip().lower()
    if backend not in ("postgres", "memory"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SECURITY_STATE_BACKEND must be 'postgres' or 'memory'.",
        )
    return backend


def login_rate_limit_settings() -> Dict[str, int]:
    return {
        "max_attempts": _parse_positive_int_env("AUTH_LOGIN_MAX_ATTEMPTS", 5),
        "window_seconds": _parse_positive_int_env("AUTH_LOGIN_WINDOW_SECONDS", 60),
        "block_seconds": _parse_positive_int_env("AUTH_LOGIN_BLOCK_SECONDS", 300),
    }


class InMemorySecurityState(SecurityStateBackend):
    def __init__(self) -> None:
        self._lock = Lock()
        self._active_by_subject: Dict[str, Dict[str, int]] = {}
        self._used_or_revoked: Dict[str, int] = {}
        self._attempts: Dict[str, Deque[float]] = {}
        self._blocked_until: Dict[str, float] = {}

    def init_schema(self) -> None:
        return

    def _cleanup_refresh(self, now_epoch: int) -> None:
        expired_used = [jti for jti, exp in self._used_or_revoked.items() if exp <= now_epoch]
        for jti in expired_used:
            self._used_or_revoked.pop(jti, None)

        empty_subjects = []
        for subject, tokens in self._active_by_subject.items():
            expired = [jti for jti, exp in tokens.items() if exp <= now_epoch]
            for jti in expired:
                tokens.pop(jti, None)
            if not tokens:
                empty_subjects.append(subject)

        for subject in empty_subjects:
            self._active_by_subject.pop(subject, None)

    def register_refresh_token(self, subject: str, jti: str, exp: int) -> None:
        now_epoch = int(_utc_now().timestamp())
        with self._lock:
            self._cleanup_refresh(now_epoch)
            self._active_by_subject.setdefault(subject, {})[jti] = exp

    def _revoke_all_for_subject(self, subject: str) -> None:
        tokens = self._active_by_subject.pop(subject, {})
        for jti, exp in tokens.items():
            self._used_or_revoked[jti] = exp

    def consume_refresh_token(self, subject: str, jti: str) -> RefreshConsumeResult:
        now_epoch = int(_utc_now().timestamp())
        with self._lock:
            self._cleanup_refresh(now_epoch)

            if jti in self._used_or_revoked:
                self._revoke_all_for_subject(subject)
                return RefreshConsumeResult(ok=False, reused=True)

            subject_tokens = self._active_by_subject.get(subject)
            if not subject_tokens:
                return RefreshConsumeResult(ok=False, reused=False)

            exp = subject_tokens.pop(jti, None)
            if exp is None:
                return RefreshConsumeResult(ok=False, reused=False)

            self._used_or_revoked[jti] = exp
            if not subject_tokens:
                self._active_by_subject.pop(subject, None)

            return RefreshConsumeResult(ok=True, reused=False)

    def _cleanup_attempts(self, key: str, now: float, window_seconds: int) -> Deque[float]:
        attempts = self._attempts.setdefault(key, deque())
        cutoff = now - window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def check_login_rate_limit(self, key: str) -> int:
        now = _utc_now().timestamp()
        with self._lock:
            blocked_until = self._blocked_until.get(key)
            if blocked_until is None:
                return 0
            if blocked_until <= now:
                self._blocked_until.pop(key, None)
                return 0
            return max(1, int(blocked_until - now))

    def register_login_failure(self, key: str, max_attempts: int, window_seconds: int, block_seconds: int) -> None:
        now = _utc_now().timestamp()
        with self._lock:
            attempts = self._cleanup_attempts(key, now, window_seconds)
            attempts.append(now)
            if len(attempts) >= max_attempts:
                self._blocked_until[key] = now + block_seconds

    def register_login_success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._blocked_until.pop(key, None)


class PostgresSecurityState(SecurityStateBackend):
    def _dsn(self) -> str:
        settings = load_database_settings()
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres security backend.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for security state.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
            jti TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'used', 'revoked')),
            expires_at TIMESTAMPTZ NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            used_at TIMESTAMPTZ NULL
        );

        CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_subject_status
            ON auth_refresh_tokens(subject, status);

        CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_expires_at
            ON auth_refresh_tokens(expires_at);

        CREATE TABLE IF NOT EXISTS auth_login_attempts (
            id BIGSERIAL PRIMARY KEY,
            rate_key TEXT NOT NULL,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_key_time
            ON auth_login_attempts(rate_key, attempted_at);

        CREATE TABLE IF NOT EXISTS auth_login_blocks (
            rate_key TEXT PRIMARY KEY,
            blocked_until TIMESTAMPTZ NOT NULL
        );
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def register_refresh_token(self, subject: str, jti: str, exp: int) -> None:
        expires_at = _utc_from_epoch(exp)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_refresh_tokens WHERE expires_at <= NOW()")
                cur.execute(
                    """
                    INSERT INTO auth_refresh_tokens (jti, subject, status, expires_at)
                    VALUES (%s, %s, 'active', %s)
                    ON CONFLICT (jti)
                    DO UPDATE SET
                        subject = EXCLUDED.subject,
                        status = 'active',
                        expires_at = EXCLUDED.expires_at,
                        used_at = NULL
                    """,
                    (jti, subject, expires_at),
                )
            conn.commit()

    def consume_refresh_token(self, subject: str, jti: str) -> RefreshConsumeResult:
        now = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_refresh_tokens WHERE expires_at <= NOW()")
                cur.execute(
                    """
                    SELECT subject, status, expires_at
                    FROM auth_refresh_tokens
                    WHERE jti = %s
                    FOR UPDATE
                    """,
                    (jti,),
                )
                row = cur.fetchone()

                if row is None:
                    conn.commit()
                    return RefreshConsumeResult(ok=False, reused=False)

                token_subject, token_status, expires_at = row
                if token_subject != subject:
                    conn.commit()
                    return RefreshConsumeResult(ok=False, reused=False)

                if token_status in ("used", "revoked"):
                    cur.execute(
                        """
                        UPDATE auth_refresh_tokens
                        SET status = 'revoked'
                        WHERE subject = %s AND status = 'active'
                        """,
                        (subject,),
                    )
                    conn.commit()
                    return RefreshConsumeResult(ok=False, reused=True)

                if token_status != "active" or expires_at <= now:
                    cur.execute(
                        """
                        UPDATE auth_refresh_tokens
                        SET status = 'revoked'
                        WHERE jti = %s
                        """,
                        (jti,),
                    )
                    conn.commit()
                    return RefreshConsumeResult(ok=False, reused=False)

                cur.execute(
                    """
                    UPDATE auth_refresh_tokens
                    SET status = 'used', used_at = NOW()
                    WHERE jti = %s
                    """,
                    (jti,),
                )
                conn.commit()
                return RefreshConsumeResult(ok=True, reused=False)

    def check_login_rate_limit(self, key: str) -> int:
        now = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_login_blocks WHERE blocked_until <= NOW()")
                cur.execute(
                    """
                    SELECT blocked_until
                    FROM auth_login_blocks
                    WHERE rate_key = %s
                    """,
                    (key,),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            return 0

        blocked_until = row[0]
        remaining_seconds = math.ceil((blocked_until - now).total_seconds())
        return max(0, remaining_seconds)

    def register_login_failure(self, key: str, max_attempts: int, window_seconds: int, block_seconds: int) -> None:
        now = _utc_now()
        window_start = now - timedelta(seconds=window_seconds)
        blocked_until = now + timedelta(seconds=block_seconds)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM auth_login_attempts
                    WHERE rate_key = %s AND attempted_at < %s
                    """,
                    (key, window_start),
                )
                cur.execute(
                    """
                    INSERT INTO auth_login_attempts (rate_key, attempted_at)
                    VALUES (%s, %s)
                    """,
                    (key, now),
                )
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM auth_login_attempts
                    WHERE rate_key = %s AND attempted_at >= %s
                    """,
                    (key, window_start),
                )
                attempts_count = int(cur.fetchone()[0])

                if attempts_count >= max_attempts:
                    cur.execute(
                        """
                        INSERT INTO auth_login_blocks (rate_key, blocked_until)
                        VALUES (%s, %s)
                        ON CONFLICT (rate_key)
                        DO UPDATE SET blocked_until = GREATEST(auth_login_blocks.blocked_until, EXCLUDED.blocked_until)
                        """,
                        (key, blocked_until),
                    )
            conn.commit()

    def register_login_success(self, key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_login_attempts WHERE rate_key = %s", (key,))
                cur.execute("DELETE FROM auth_login_blocks WHERE rate_key = %s", (key,))
            conn.commit()


_memory_backend = InMemorySecurityState()
_postgres_backend = PostgresSecurityState()


def get_security_state_backend() -> SecurityStateBackend:
    backend = _security_backend_name()
    if backend == "memory":
        return _memory_backend
    return _postgres_backend


def validate_security_state_settings() -> None:
    backend = _security_backend_name()
    if backend == "memory":
        return

    settings = load_database_settings()
    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DATABASE_ENABLED must be true when SECURITY_STATE_BACKEND=postgres.",
        )

    if psycopg is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="psycopg is required when SECURITY_STATE_BACKEND=postgres.",
        )


def initialize_security_state() -> None:
    get_security_state_backend().init_schema()
