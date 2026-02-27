"""Unit tests for conversation loop runner v1."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.loop_runner import (
    ConversationNotActiveError,
    ConversationLoopRunner,
    NoParticipantsError,
    RunLoopInput,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_exists: bool,
        conversation_status: str = "active",
        participants: list[tuple[Any, str, str]],
        initial_turn_index: int = 0,
        initial_seq_no: int = 0,
        recent_turn_texts: list[str] | None = None,
    ):
        self.conversation_exists = conversation_exists
        self.conversation_status = conversation_status
        self.participants = participants
        self.initial_turn_index = initial_turn_index
        self.initial_seq_no = initial_seq_no
        self.recent_turn_texts = recent_turn_texts or []
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self._last_fetchall: Any = []
        self.next_message_id = 1
        self.inserted_event_actor_ids: list[str] = []
        self.inserted_turn_indexes: list[int] = []
        self.inserted_message_statuses: list[str] = []
        self.inserted_content_texts: list[str] = []
        self.inserted_event_types: list[str] = []
        self.updated_status: str | None = None

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
                ("conversation", self.conversation_status) if self.conversation_exists else None
            )
            return
        if "select id, kind, display_name from participant" in normalized_sql:
            self._last_fetchall = self.participants
            return
        if "select coalesce(max(turn_index), 0) from message" in normalized_sql:
            self._last_fetchone = (self.initial_turn_index,)
            return
        if "select coalesce(max(seq_no), 0) from event" in normalized_sql:
            self._last_fetchone = (self.initial_seq_no,)
            return
        if "select content_text from message" in normalized_sql:
            self._last_fetchall = [(text,) for text in self.recent_turn_texts]
            return
        if "insert into message (" in normalized_sql:
            assert params is not None
            self.inserted_turn_indexes.append(int(params[2]))
            self.inserted_message_statuses.append(str(params[3]))
            self.inserted_content_texts.append(str(params[4]))
            self._last_fetchone = (str(self.next_message_id),)
            self.next_message_id += 1
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            self.inserted_event_actor_ids.append(str(params[2]))
            self.initial_seq_no = int(params[3])
            self.inserted_event_types.append(str(params[4]))
            return
        if "update conversation set" in normalized_sql:
            assert params is not None
            self.updated_status = str(params[0])
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_loop_runner_creates_round_robin_turns() -> None:
    participant_a = (uuid4(), "ai", "AI(1)")
    participant_b = (uuid4(), "ai", "AI(2)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_a, participant_b],
        initial_turn_index=0,
        initial_seq_no=2,
    )
    runner = ConversationLoopRunner(connection)
    conversation_id = uuid4()

    result = runner.run_loop(RunLoopInput(conversation_id=conversation_id, max_turns=3))

    assert result.conversation_id == conversation_id
    assert result.turns_created == 3
    assert result.turns_rejected == 0
    assert result.turn_index_last == 3
    assert result.event_seq_last == 5
    assert result.started_at <= result.finished_at
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0
    assert connection.inserted_turn_indexes == [1, 2, 3]
    assert connection.inserted_message_statuses == ["committed", "committed", "committed"]
    assert connection.inserted_event_types == [
        "turn.committed",
        "turn.committed",
        "turn.committed",
    ]
    assert connection.inserted_event_actor_ids == [
        str(participant_a[0]),
        str(participant_b[0]),
        str(participant_a[0]),
    ]
    assert connection.updated_status == "active"


def test_loop_runner_rejects_when_ai_citation_required_but_missing() -> None:
    participant_a = (uuid4(), "ai", "AI(1)")
    participant_b = (uuid4(), "ai", "AI(2)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_a, participant_b],
        initial_turn_index=0,
        initial_seq_no=4,
    )
    runner = ConversationLoopRunner(connection)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=2,
            require_citations=True,
            required_citation_ids=[],
        )
    )

    assert result.turns_created == 0
    assert result.turns_rejected == 2
    assert result.turn_index_last == 2
    assert result.event_seq_last == 6
    assert connection.inserted_message_statuses == ["rejected", "rejected"]
    assert connection.inserted_event_types == ["turn.rejected", "turn.rejected"]
    assert connection.updated_status == "paused"


def test_loop_runner_includes_required_citation_hint_for_ai_turns() -> None:
    citation_id = uuid4()
    participant_a = (uuid4(), "ai", "AI(1)")
    participant_b = (uuid4(), "human", "Reviewer")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_a, participant_b],
        initial_turn_index=0,
        initial_seq_no=1,
    )
    runner = ConversationLoopRunner(connection)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=2,
            require_citations=True,
            required_citation_ids=[citation_id],
        )
    )

    assert result.turns_created == 2
    assert result.turns_rejected == 0
    assert f"[chunk:{citation_id}]" in connection.inserted_content_texts[0]
    assert connection.inserted_message_statuses == ["committed", "committed"]


def test_loop_runner_rejects_missing_conversation() -> None:
    connection = FakeConnection(conversation_exists=False, participants=[])
    runner = ConversationLoopRunner(connection)

    with pytest.raises(ConversationNotFoundError):
        runner.run_loop(RunLoopInput(conversation_id=uuid4(), max_turns=1))

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_loop_runner_rejects_non_active_conversation() -> None:
    participant_a = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        conversation_status="paused",
        participants=[participant_a],
    )
    runner = ConversationLoopRunner(connection)

    with pytest.raises(ConversationNotActiveError):
        runner.run_loop(RunLoopInput(conversation_id=uuid4(), max_turns=1))

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_loop_runner_rejects_no_participants() -> None:
    connection = FakeConnection(conversation_exists=True, participants=[])
    runner = ConversationLoopRunner(connection)

    with pytest.raises(NoParticipantsError):
        runner.run_loop(RunLoopInput(conversation_id=uuid4(), max_turns=2))

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_loop_runner_rejects_invalid_turn_count() -> None:
    connection = FakeConnection(conversation_exists=True, participants=[])
    runner = ConversationLoopRunner(connection)

    with pytest.raises(ValueError):
        runner.run_loop(RunLoopInput(conversation_id=uuid4(), max_turns=0))
