"""Tests for scheduler repository helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from services.scheduler.repository import build_idempotency_key, normalize_scheduled_for


def test_normalize_scheduled_for_rounds_to_minute_utc() -> None:
    value = datetime(2026, 2, 27, 18, 1, 42, 999, tzinfo=timezone.utc)
    normalized = normalize_scheduled_for(value)
    assert normalized == datetime(2026, 2, 27, 18, 1, 0, tzinfo=timezone.utc)


def test_normalize_scheduled_for_handles_naive_timestamp() -> None:
    value = datetime(2026, 2, 27, 18, 1, 10)
    normalized = normalize_scheduled_for(value)
    assert normalized.tzinfo is not None
    assert normalized.second == 0
    assert normalized.microsecond == 0


def test_build_idempotency_key_is_stable_for_same_minute() -> None:
    template_id = uuid4()
    first = build_idempotency_key(
        template_id, datetime(2026, 2, 27, 18, 1, 1, tzinfo=timezone.utc)
    )
    second = build_idempotency_key(
        template_id, datetime(2026, 2, 27, 18, 1, 59, tzinfo=timezone.utc)
    )
    assert first == second


def test_build_idempotency_key_changes_for_different_minute() -> None:
    template_id = uuid4()
    first = build_idempotency_key(
        template_id, datetime(2026, 2, 27, 18, 1, 0, tzinfo=timezone.utc)
    )
    second = build_idempotency_key(
        template_id, datetime(2026, 2, 27, 18, 2, 0, tzinfo=timezone.utc)
    )
    assert first != second
