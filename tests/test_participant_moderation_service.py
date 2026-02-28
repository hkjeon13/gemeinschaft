"""Unit tests for participant moderation service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_moderation_service import (
    ApplyParticipantModerationInput,
    InvalidModerationActionError,
    ParticipantModerationNotFoundError,
    ParticipantModerationService,
    ParticipantModerationStateError,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        participant_metadata: dict[str, Any] | None,
        initial_seq_no: int = 0,
    ):
        self.conversation_exists = conversation_exists
        self.participant_metadata = participant_metadata
        self.initial_seq_no = initial_seq_no
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self.updated_participant_metadata: dict[str, Any] | None = None
        self.inserted_event_types: list[str] = []

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id from conversation where id = %s for update" in normalized_sql:
            self._last_fetchone = ("conversation",) if self.conversation_exists else None
            return
        if "select id, metadata from participant" in normalized_sql:
            if self.participant_metadata is None:
                self._last_fetchone = None
            else:
                self._last_fetchone = (uuid4(), self.participant_metadata)
            return
        if "update participant set metadata = %s::jsonb where id = %s" in normalized_sql:
            assert params is not None
            self.updated_participant_metadata = json.loads(str(params[0]))
            return
        if (
            "select coalesce(max(seq_no), 0) from event where conversation_id = %s"
            in normalized_sql
        ):
            self._last_fetchone = (self.initial_seq_no,)
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            self.inserted_event_types.append(str(params[3]))
            self._last_fetchone = (datetime(2026, 2, 28, 2, 0, tzinfo=timezone.utc),)
            return
        if "update conversation set updated_at = now() where id = %s" in normalized_sql:
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_participant_moderation_mute_success() -> None:
    participant_id = uuid4()
    conversation_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_metadata={},
        initial_seq_no=4,
    )
    service = ParticipantModerationService(connection)

    result = service.apply(
        ApplyParticipantModerationInput(
            conversation_id=conversation_id,
            participant_id=participant_id,
            action="mute",
            actor_participant_id=uuid4(),
            reason="off-topic loop",
            metadata={"source": "moderator"},
        )
    )

    assert result.conversation_id == conversation_id
    assert result.participant_id == participant_id
    assert result.muted is True
    assert result.event_type == "participant.muted"
    assert result.event_seq_last == 5
    assert connection.updated_participant_metadata is not None
    assert connection.updated_participant_metadata["moderation"]["muted"] is True
    assert connection.inserted_event_types == ["participant.muted"]
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_participant_moderation_unmute_success() -> None:
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_metadata={"moderation": {"muted": True}},
        initial_seq_no=8,
    )
    service = ParticipantModerationService(connection)

    result = service.apply(
        ApplyParticipantModerationInput(
            conversation_id=uuid4(),
            participant_id=participant_id,
            action="unmute",
            actor_participant_id=uuid4(),
            reason="resolved",
            metadata={},
        )
    )

    assert result.muted is False
    assert result.event_type == "participant.unmuted"
    assert result.event_seq_last == 9
    assert connection.updated_participant_metadata is not None
    assert connection.updated_participant_metadata["moderation"]["muted"] is False
    assert connection.inserted_event_types == ["participant.unmuted"]


def test_participant_moderation_invalid_action() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        participant_metadata={},
    )
    service = ParticipantModerationService(connection)

    with pytest.raises(InvalidModerationActionError):
        service.apply(
            ApplyParticipantModerationInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                action="pause",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


def test_participant_moderation_conversation_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=False,
        participant_metadata={},
    )
    service = ParticipantModerationService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.apply(
            ApplyParticipantModerationInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                action="mute",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_participant_moderation_participant_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        participant_metadata=None,
    )
    service = ParticipantModerationService(connection)

    with pytest.raises(ParticipantModerationNotFoundError):
        service.apply(
            ApplyParticipantModerationInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                action="mute",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_participant_moderation_state_error() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        participant_metadata={"moderation": {"muted": True}},
    )
    service = ParticipantModerationService(connection)

    with pytest.raises(ParticipantModerationStateError):
        service.apply(
            ApplyParticipantModerationInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                action="mute",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
