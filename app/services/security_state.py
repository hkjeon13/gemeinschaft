import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict

from fastapi import HTTPException, status


@dataclass
class RefreshConsumeResult:
    ok: bool
    reused: bool


class RefreshTokenRegistry:
    """
    In-memory refresh-token registry for rotation/reuse detection.
    NOTE: Multi-worker/multi-instance deployment에서는 공용 저장소(예: Postgres/Redis)로 교체해야 한다.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_by_subject: Dict[str, Dict[str, int]] = {}
        self._used_or_revoked: Dict[str, int] = {}

    def _cleanup(self, now: int) -> None:
        expired_used = [jti for jti, exp in self._used_or_revoked.items() if exp <= now]
        for jti in expired_used:
            self._used_or_revoked.pop(jti, None)

        empty_subjects = []
        for subject, tokens in self._active_by_subject.items():
            expired = [jti for jti, exp in tokens.items() if exp <= now]
            for jti in expired:
                tokens.pop(jti, None)
            if not tokens:
                empty_subjects.append(subject)

        for subject in empty_subjects:
            self._active_by_subject.pop(subject, None)

    def register(self, subject: str, jti: str, exp: int) -> None:
        now = int(time.time())
        with self._lock:
            self._cleanup(now)
            self._active_by_subject.setdefault(subject, {})[jti] = exp

    def _revoke_all_for_subject(self, subject: str) -> None:
        tokens = self._active_by_subject.pop(subject, {})
        for jti, exp in tokens.items():
            self._used_or_revoked[jti] = exp

    def consume(self, subject: str, jti: str) -> RefreshConsumeResult:
        now = int(time.time())
        with self._lock:
            self._cleanup(now)

            if jti in self._used_or_revoked:
                # Reuse detected: invalidate all currently active refresh tokens for this subject.
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


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, block_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._lock = Lock()
        self._attempts: Dict[str, Deque[float]] = {}
        self._blocked_until: Dict[str, float] = {}

    def _cleanup_attempts(self, key: str, now: float) -> Deque[float]:
        attempts = self._attempts.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def check(self, key: str) -> int:
        now = time.time()
        with self._lock:
            blocked_until = self._blocked_until.get(key)
            if blocked_until is None:
                return 0
            if blocked_until <= now:
                self._blocked_until.pop(key, None)
                return 0
            return max(1, int(blocked_until - now))

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            attempts = self._cleanup_attempts(key, now)
            attempts.append(now)
            if len(attempts) >= self.max_attempts:
                self._blocked_until[key] = now + self.block_seconds

    def register_success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._blocked_until.pop(key, None)


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


def login_rate_limit_settings() -> Dict[str, int]:
    return {
        "max_attempts": _parse_positive_int_env("AUTH_LOGIN_MAX_ATTEMPTS", 5),
        "window_seconds": _parse_positive_int_env("AUTH_LOGIN_WINDOW_SECONDS", 60),
        "block_seconds": _parse_positive_int_env("AUTH_LOGIN_BLOCK_SECONDS", 300),
    }


def create_login_rate_limiter() -> LoginRateLimiter:
    config = login_rate_limit_settings()
    return LoginRateLimiter(
        max_attempts=config["max_attempts"],
        window_seconds=config["window_seconds"],
        block_seconds=config["block_seconds"],
    )


refresh_token_registry = RefreshTokenRegistry()
login_rate_limiter = create_login_rate_limiter()
