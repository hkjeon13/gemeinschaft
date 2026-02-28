"""Unit tests for participant roster read service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_roster_service import (
    ParticipantRosterService,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        participant_rows: list[tuple[Any, ...]],
    ):
        self.conversation_exists = conversation_exists
        self.participant_rows = participant_rows
        self._last_fetchone: Any = None
        self._last_fetchall: list[Any] = []
        self.last_participant_query: str | None = None
        self.last_participant_params: tuple[Any, ...] | None = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id from conversation where id = %s" in normalized_sql:
            self._last_fetchone = ("conversation",) if self.conversation_exists else None
            return
        if "from participant p" in normalized_sql:
            self.last_participant_query = normalized_sql
            self.last_participant_params = params
            self._last_fetchall = self.participant_rows
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall


def test_list_participants_success_default_excludes_left() -> None:
    now = datetime(2026, 2, 28, 3, 0, tzinfo=timezone.utc)
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_rows=[
            (
                participant_id,
                "ai",
                "AI(1)",
                "critic",
                now,
                None,
                True,
                {"moderation": {"muted": True}},
            )
        ],
    )
    service = ParticipantRosterService(connection)

    rows = service.list_participants(conversation_id=uuid4())

    assert len(rows) == 1
    assert rows[0].participant_id == participant_id
    assert rows[0].display_name == "AI(1)"
    assert rows[0].role_label == "critic"
    assert rows[0].muted is True
    assert connection.last_participant_query is not None
    assert "p.left_at is null" in connection.last_participant_query


def test_list_participants_include_left() -> None:
    now = datetime(2026, 2, 28, 3, 10, tzinfo=timezone.utc)
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_rows=[
            (
                participant_id,
                "human",
                "Reviewer",
                "moderator",
                now,
                now,
                False,
                {},
            )
        ],
    )
    service = ParticipantRosterService(connection)

    rows = service.list_participants(conversation_id=uuid4(), include_left=True)

    assert len(rows) == 1
    assert rows[0].participant_id == participant_id
    assert rows[0].kind == "human"
    assert rows[0].left_at == now
    assert connection.last_participant_query is not None
    assert "p.left_at is null" not in connection.last_participant_query


def test_list_participants_applies_cursor_filter() -> None:
    now = datetime(2026, 2, 28, 3, 15, tzinfo=timezone.utc)
    participant_id = uuid4()
    connection = FakeConnection(conversation_exists=True, participant_rows=[])
    service = ParticipantRosterService(connection)

    service.list_participants(
        conversation_id=uuid4(),
        include_left=False,
        limit=20,
        after_joined_at=now,
        after_participant_id=participant_id,
    )

    assert connection.last_participant_query is not None
    assert (
        "p.joined_at > %s or (p.joined_at = %s and p.id > %s)"
        in connection.last_participant_query
    )
    assert connection.last_participant_params is not None
    assert connection.last_participant_params[1] == now
    assert connection.last_participant_params[2] == now
    assert connection.last_participant_params[3] == str(participant_id)


def test_list_participants_rejects_invalid_limit() -> None:
    connection = FakeConnection(conversation_exists=True, participant_rows=[])
    service = ParticipantRosterService(connection)

    with pytest.raises(ValueError):
        service.list_participants(conversation_id=uuid4(), limit=0)


def test_list_participants_rejects_partial_cursor() -> None:
    connection = FakeConnection(conversation_exists=True, participant_rows=[])
    service = ParticipantRosterService(connection)

    with pytest.raises(ValueError):
        service.list_participants(
            conversation_id=uuid4(),
            after_joined_at=datetime(2026, 2, 28, 3, 15, tzinfo=timezone.utc),
            after_participant_id=None,
        )


def test_list_participants_conversation_missing() -> None:
    connection = FakeConnection(conversation_exists=False, participant_rows=[])
    service = ParticipantRosterService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.list_participants(conversation_id=uuid4())
