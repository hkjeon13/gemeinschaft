"""Unit tests for participant role switching service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_role_service import (
    ParticipantNotFoundError,
    ParticipantRoleService,
    SwitchParticipantRoleInput,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        participant_row: tuple[Any, str | None] | None,
        initial_seq_no: int = 0,
    ):
        self.conversation_exists = conversation_exists
        self.participant_row = participant_row
        self.initial_seq_no = initial_seq_no
        self.commit_calls = 0
        self.rollback_calls = 0
        self.updated_role_label: str | None = None
        self.inserted_event_types: list[str] = []
        self._last_fetchone: Any = None

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
        if "select id, role_label from participant" in normalized_sql:
            self._last_fetchone = self.participant_row
            return
        if "update participant set role_label = %s where id = %s" in normalized_sql:
            assert params is not None
            self.updated_role_label = str(params[0])
            return
        if (
            "select coalesce(max(seq_no), 0) from event where conversation_id = %s"
            in normalized_sql
        ):
            self._last_fetchone = (self.initial_seq_no,)
            return
        if (
            "insert into event (" in normalized_sql
            and "participant.role_switched" in normalized_sql
        ):
            self.inserted_event_types.append("participant.role_switched")
            self._last_fetchone = (datetime(2026, 2, 28, 1, 0, tzinfo=timezone.utc),)
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


def test_switch_participant_role_success() -> None:
    participant_id = uuid4()
    conversation_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_row=(participant_id, "ai_observer"),
        initial_seq_no=9,
    )
    service = ParticipantRoleService(connection)

    result = service.switch_role(
        SwitchParticipantRoleInput(
            conversation_id=conversation_id,
            participant_id=participant_id,
            new_role_label="ai_critic",
            actor_participant_id=uuid4(),
            reason="rebalance debate",
            metadata={"source": "moderator"},
        )
    )

    assert result.conversation_id == conversation_id
    assert result.participant_id == participant_id
    assert result.previous_role_label == "ai_observer"
    assert result.new_role_label == "ai_critic"
    assert result.event_seq_last == 10
    assert connection.updated_role_label == "ai_critic"
    assert connection.inserted_event_types == ["participant.role_switched"]
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_switch_participant_role_conversation_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=False,
        participant_row=(uuid4(), "human"),
    )
    service = ParticipantRoleService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.switch_role(
            SwitchParticipantRoleInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                new_role_label="moderator",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_switch_participant_role_participant_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        participant_row=None,
    )
    service = ParticipantRoleService(connection)

    with pytest.raises(ParticipantNotFoundError):
        service.switch_role(
            SwitchParticipantRoleInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                new_role_label="moderator",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_switch_participant_role_rejects_same_role() -> None:
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participant_row=(participant_id, "moderator"),
    )
    service = ParticipantRoleService(connection)

    with pytest.raises(ValueError):
        service.switch_role(
            SwitchParticipantRoleInput(
                conversation_id=uuid4(),
                participant_id=participant_id,
                new_role_label="moderator",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_switch_participant_role_rejects_empty_role() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        participant_row=(uuid4(), "ai"),
    )
    service = ParticipantRoleService(connection)

    with pytest.raises(ValueError):
        service.switch_role(
            SwitchParticipantRoleInput(
                conversation_id=uuid4(),
                participant_id=uuid4(),
                new_role_label="  ",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0
