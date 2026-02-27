"""Unit tests for human intervention service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.intervention_service import (
    ApplyInterventionInput,
    HumanInterventionService,
    InvalidInterventionStateError,
    InvalidInterventionTypeError,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        conversation_status: str,
        initial_seq_no: int = 0,
    ):
        self.conversation_exists = conversation_exists
        self.conversation_status = conversation_status
        self.initial_seq_no = initial_seq_no
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self.inserted_event_types: list[str] = []
        self.updated_status: str | None = None
        self.updated_touch_only = False

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select id, status from conversation where id" in normalized_sql:
            self._last_fetchone = (
                ("conversation", self.conversation_status)
                if self.conversation_exists
                else None
            )
            return
        if "select coalesce(max(seq_no), 0) from event" in normalized_sql:
            self._last_fetchone = (self.initial_seq_no,)
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            self.initial_seq_no = int(params[2])
            event_type = (
                "human.intervention" if "human.intervention" in normalized_sql else str(params[3])
            )
            self.inserted_event_types.append(event_type)
            self._last_fetchone = (datetime(2026, 2, 27, 21, 0, tzinfo=timezone.utc),)
            return
        if "update conversation set updated_at = now() where id = %s" in normalized_sql:
            self.updated_touch_only = True
            return
        if (
            "update conversation set updated_at = now(), status = %s where id = %s"
        ) in normalized_sql:
            assert params is not None
            self.updated_status = str(params[0])
            return
        if (
            "update conversation set updated_at = now(), ended_at = coalesce(ended_at, now()), "
            "status = %s where id = %s"
        ) in normalized_sql:
            assert params is not None
            self.updated_status = str(params[0])
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_interrupt_intervention_pauses_conversation() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        conversation_status="active",
        initial_seq_no=10,
    )
    service = HumanInterventionService(connection)
    conversation_id = uuid4()

    result = service.apply_intervention(
        ApplyInterventionInput(
            conversation_id=conversation_id,
            intervention_type="interrupt",
            actor_participant_id=uuid4(),
            instruction="Hold and wait for review",
            metadata={"reason": "manual-check"},
        )
    )

    assert result.conversation_id == conversation_id
    assert result.status == "paused"
    assert result.event_seq_last == 12
    assert result.applied_events == ["human.intervention", "conversation.paused"]
    assert connection.inserted_event_types == ["human.intervention", "conversation.paused"]
    assert connection.updated_status == "paused"
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_steer_intervention_keeps_current_status() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        conversation_status="paused",
        initial_seq_no=3,
    )
    service = HumanInterventionService(connection)

    result = service.apply_intervention(
        ApplyInterventionInput(
            conversation_id=uuid4(),
            intervention_type="steer",
            actor_participant_id=None,
            instruction="Switch to fraud topic",
            metadata={},
        )
    )

    assert result.status == "paused"
    assert result.event_seq_last == 4
    assert result.applied_events == ["human.intervention"]
    assert connection.inserted_event_types == ["human.intervention"]
    assert connection.updated_touch_only is True


def test_invalid_intervention_type_rejected() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        conversation_status="active",
        initial_seq_no=1,
    )
    service = HumanInterventionService(connection)

    with pytest.raises(InvalidInterventionTypeError):
        service.apply_intervention(
            ApplyInterventionInput(
                conversation_id=uuid4(),
                intervention_type="unknown",
                actor_participant_id=None,
                instruction=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


def test_invalid_transition_rejected() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        conversation_status="active",
        initial_seq_no=1,
    )
    service = HumanInterventionService(connection)

    with pytest.raises(InvalidInterventionStateError):
        service.apply_intervention(
            ApplyInterventionInput(
                conversation_id=uuid4(),
                intervention_type="resume",
                actor_participant_id=None,
                instruction=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_missing_conversation_rejected() -> None:
    connection = FakeConnection(
        conversation_exists=False,
        conversation_status="active",
        initial_seq_no=1,
    )
    service = HumanInterventionService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.apply_intervention(
            ApplyInterventionInput(
                conversation_id=uuid4(),
                intervention_type="interrupt",
                actor_participant_id=None,
                instruction=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
