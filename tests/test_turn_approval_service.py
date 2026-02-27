"""Unit tests for turn approval workflow service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.turn_approval_service import (
    ApplyTurnApprovalInput,
    InvalidApprovalDecisionError,
    TurnApprovalService,
    TurnApprovalStateError,
    TurnNotFoundError,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        message_row: tuple[Any, str, Any] | None,
        initial_seq_no: int = 0,
    ):
        self.conversation_exists = conversation_exists
        self.message_row = message_row
        self.initial_seq_no = initial_seq_no
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self.updated_message_status: str | None = None
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
        if "select id, status, participant_id from message" in normalized_sql:
            self._last_fetchone = self.message_row
            return
        if "select coalesce(max(seq_no), 0) from event" in normalized_sql:
            self._last_fetchone = (self.initial_seq_no,)
            return
        if "update message set status = 'committed' where id = %s" in normalized_sql:
            self.updated_message_status = "committed"
            return
        if "update message set status = 'rejected' where id = %s" in normalized_sql:
            self.updated_message_status = "rejected"
            return
        if "insert into event (" in normalized_sql:
            if "turn.approved" in normalized_sql:
                self.inserted_event_types.append("turn.approved")
            elif "turn.committed" in normalized_sql:
                self.inserted_event_types.append("turn.committed")
            elif "turn.rejected" in normalized_sql:
                self.inserted_event_types.append("turn.rejected")
            else:
                raise AssertionError(f"Unexpected event SQL: {normalized_sql}")
            assert params is not None
            self.initial_seq_no = int(params[3])
            self._last_fetchone = (datetime(2026, 2, 27, 23, 0, tzinfo=timezone.utc),)
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_turn_approval_approve_success() -> None:
    message_id = uuid4()
    participant_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        message_row=(message_id, "proposed", participant_id),
        initial_seq_no=10,
    )
    service = TurnApprovalService(connection)
    conversation_id = uuid4()

    result = service.apply_decision(
        ApplyTurnApprovalInput(
            conversation_id=conversation_id,
            turn_index=3,
            decision="approve",
            actor_participant_id=uuid4(),
            reason="looks good",
            metadata={"review": "ok"},
        )
    )

    assert result.conversation_id == conversation_id
    assert result.turn_index == 3
    assert result.message_status == "committed"
    assert result.event_seq_last == 12
    assert result.applied_events == ["turn.approved", "turn.committed"]
    assert connection.updated_message_status == "committed"
    assert connection.inserted_event_types == ["turn.approved", "turn.committed"]
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0


def test_turn_approval_reject_success() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        message_row=(uuid4(), "proposed", uuid4()),
        initial_seq_no=2,
    )
    service = TurnApprovalService(connection)

    result = service.apply_decision(
        ApplyTurnApprovalInput(
            conversation_id=uuid4(),
            turn_index=1,
            decision="reject",
            actor_participant_id=uuid4(),
            reason="off topic",
            metadata={},
        )
    )

    assert result.message_status == "rejected"
    assert result.event_seq_last == 3
    assert result.applied_events == ["turn.rejected"]
    assert connection.updated_message_status == "rejected"
    assert connection.inserted_event_types == ["turn.rejected"]


def test_turn_approval_invalid_decision() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        message_row=(uuid4(), "proposed", uuid4()),
    )
    service = TurnApprovalService(connection)

    with pytest.raises(InvalidApprovalDecisionError):
        service.apply_decision(
            ApplyTurnApprovalInput(
                conversation_id=uuid4(),
                turn_index=1,
                decision="skip",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


def test_turn_approval_turn_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        message_row=None,
    )
    service = TurnApprovalService(connection)

    with pytest.raises(TurnNotFoundError):
        service.apply_decision(
            ApplyTurnApprovalInput(
                conversation_id=uuid4(),
                turn_index=9,
                decision="approve",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_turn_approval_state_error_when_not_proposed() -> None:
    connection = FakeConnection(
        conversation_exists=True,
        message_row=(uuid4(), "committed", uuid4()),
    )
    service = TurnApprovalService(connection)

    with pytest.raises(TurnApprovalStateError):
        service.apply_decision(
            ApplyTurnApprovalInput(
                conversation_id=uuid4(),
                turn_index=2,
                decision="approve",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_turn_approval_conversation_not_found() -> None:
    connection = FakeConnection(
        conversation_exists=False,
        message_row=(uuid4(), "proposed", uuid4()),
    )
    service = TurnApprovalService(connection)

    with pytest.raises(ConversationNotFoundError):
        service.apply_decision(
            ApplyTurnApprovalInput(
                conversation_id=uuid4(),
                turn_index=2,
                decision="approve",
                actor_participant_id=None,
                reason=None,
                metadata={},
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
