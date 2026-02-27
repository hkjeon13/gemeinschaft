"""Unit tests for conversation loop runner v1."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from services.conversation_orchestrator.context_packet_builder import (
    ContextEvidence,
    ContextPacketResult,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.loop_runner import (
    AgentRuntimeNotConfiguredError,
    ConversationNotActiveError,
    ConversationLoopRunner,
    ContextBuilderNotConfiguredError,
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
        self.inserted_message_metadata: list[dict[str, Any]] = []
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
        if "select id, status, objective from conversation where id" in normalized_sql:
            self._last_fetchone = (
                ("conversation", self.conversation_status, "Test objective")
                if self.conversation_exists
                else None
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
            self.inserted_message_metadata.append(json.loads(str(params[5])))
            self._last_fetchone = (str(self.next_message_id),)
            self.next_message_id += 1
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            if "'turn.arbitration_requested'" in normalized_sql:
                self.inserted_event_actor_ids.append(str(params[1]))
                self.initial_seq_no = int(params[2])
                self.inserted_event_types.append("turn.arbitration_requested")
                return
            if "'loop.guard_triggered'" in normalized_sql:
                self.inserted_event_actor_ids.append(str(params[1]))
                self.initial_seq_no = int(params[2])
                self.inserted_event_types.append("loop.guard_triggered")
                return
            if "'conversation.paused'" in normalized_sql:
                self.inserted_event_actor_ids.append(str(params[1]))
                self.initial_seq_no = int(params[2])
                self.inserted_event_types.append("conversation.paused")
                return
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
    assert result.turns_attempted == 3
    assert result.turns_created == 3
    assert result.turns_pending_approval == 0
    assert result.turns_rejected == 0
    assert result.turn_index_last == 3
    assert result.event_seq_last == 5
    assert result.stop_reason is None
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
    assert result.turns_attempted == 2
    assert result.turns_pending_approval == 0
    assert result.turns_rejected == 2
    assert result.turn_index_last == 2
    assert result.event_seq_last == 6
    assert result.stop_reason is None
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
    assert result.turns_attempted == 2
    assert result.turns_pending_approval == 0
    assert result.turns_rejected == 0
    assert result.stop_reason is None
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


class FakeAgentRuntimeClient:
    def __init__(self, outputs: list[str] | None = None):
        self.calls: list[Any] = []
        self.outputs = outputs or ["runtime generated answer"]

    def run_agent(self, payload: Any) -> Any:
        self.calls.append(payload)
        index = min(len(self.calls) - 1, len(self.outputs) - 1)
        output_text = self.outputs[index]
        return type(
            "RuntimeResult",
            (),
            {
                "run_id": "run-1",
                "agent_key": payload.agent_key,
                "selected_model": "model-a",
                "output_text": output_text,
                "token_in": 10,
                "token_out": 11,
                "latency_ms": 12,
                "finish_reason": "completed",
            },
        )()


class FakeContextBuilder:
    def __init__(self, evidence_chunk_id: Any):
        self.calls: list[Any] = []
        self._evidence_chunk_id = evidence_chunk_id

    def build_packet(self, payload: Any) -> ContextPacketResult:
        self.calls.append(payload)
        return ContextPacketResult(
            conversation_id=payload.conversation_id,
            source_document_id=payload.source_document_id,
            topic_id=uuid4(),
            topic_label="refund",
            topic_summary="refund topic",
            recent_turns=[],
            evidence_chunks=[
                ContextEvidence(
                    source_chunk_id=self._evidence_chunk_id,
                    chunk_index=0,
                    content_text="evidence",
                    relevance_score=0.99,
                )
            ],
        )


def test_loop_runner_uses_agent_runtime_when_enabled() -> None:
    participant_ai = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runtime = FakeAgentRuntimeClient()
    runner = ConversationLoopRunner(connection, agent_runtime_client=runtime)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=1,
            use_agent_runtime=True,
            agent_max_output_tokens=200,
        )
    )

    assert result.turns_created == 1
    assert result.turns_attempted == 1
    assert result.turns_pending_approval == 0
    assert result.stop_reason is None
    assert len(runtime.calls) == 1
    assert runtime.calls[0].agent_key == "ai_1"
    assert runtime.calls[0].max_output_tokens == 200
    assert connection.inserted_content_texts[0].startswith("runtime generated answer")
    assert connection.inserted_message_metadata[0]["generation"]["generator"] == "agent_runtime"


def test_loop_runner_uses_context_evidence_for_required_citation() -> None:
    evidence_id = uuid4()
    participant_ai = (uuid4(), "ai", "AI(1)")
    source_document_id = uuid4()
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    context_builder = FakeContextBuilder(evidence_chunk_id=evidence_id)
    runner = ConversationLoopRunner(connection, context_builder=context_builder)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=1,
            source_document_id=source_document_id,
            require_citations=True,
        )
    )

    assert result.turns_created == 1
    assert result.turns_attempted == 1
    assert result.turns_pending_approval == 0
    assert result.stop_reason is None
    assert len(context_builder.calls) == 1
    assert context_builder.calls[0].source_document_id == source_document_id
    assert f"[chunk:{evidence_id}]" in connection.inserted_content_texts[0]
    assert connection.inserted_message_statuses == ["committed"]


def test_loop_runner_rejects_context_assembly_when_builder_missing() -> None:
    participant_ai = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runner = ConversationLoopRunner(connection)

    with pytest.raises(ContextBuilderNotConfiguredError):
        runner.run_loop(
            RunLoopInput(
                conversation_id=uuid4(),
                max_turns=1,
                source_document_id=uuid4(),
            )
        )


def test_loop_runner_rejects_runtime_generation_when_client_missing() -> None:
    participant_ai = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runner = ConversationLoopRunner(connection)

    with pytest.raises(AgentRuntimeNotConfiguredError):
        runner.run_loop(
            RunLoopInput(
                conversation_id=uuid4(),
                max_turns=1,
                use_agent_runtime=True,
            )
        )


def test_loop_runner_marks_ai_turn_pending_when_human_approval_required() -> None:
    participant_ai = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runner = ConversationLoopRunner(connection)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=1,
            require_human_approval=True,
        )
    )

    assert result.turns_created == 0
    assert result.turns_attempted == 1
    assert result.turns_pending_approval == 1
    assert result.turns_rejected == 0
    assert result.stop_reason is None
    assert connection.inserted_message_statuses == ["proposed"]
    assert connection.inserted_event_types == ["turn.pending_approval"]
    assert connection.updated_status == "active"


def test_loop_runner_stops_early_on_consecutive_rejection_guard() -> None:
    participant_ai = (uuid4(), "ai", "AI(1)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_ai],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runner = ConversationLoopRunner(connection)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=5,
            require_citations=True,
            max_consecutive_rejections=2,
        )
    )

    assert result.turns_attempted == 2
    assert result.turns_created == 0
    assert result.turns_rejected == 2
    assert result.stop_reason == "consecutive_rejections_guard"
    assert connection.inserted_event_types == [
        "turn.rejected",
        "turn.rejected",
        "loop.guard_triggered",
        "conversation.paused",
    ]
    assert connection.updated_status == "paused"


def test_loop_runner_requests_arbitration_on_disjoint_ai_citations() -> None:
    citation_a = uuid4()
    citation_b = uuid4()
    participant_a = (uuid4(), "ai", "AI(1)")
    participant_b = (uuid4(), "ai", "AI(2)")
    connection = FakeConnection(
        conversation_exists=True,
        participants=[participant_a, participant_b],
        initial_turn_index=0,
        initial_seq_no=0,
    )
    runtime = FakeAgentRuntimeClient(
        outputs=[
            f"claim a [chunk:{citation_a}]",
            f"claim b [chunk:{citation_b}]",
        ]
    )
    runner = ConversationLoopRunner(connection, agent_runtime_client=runtime)

    result = runner.run_loop(
        RunLoopInput(
            conversation_id=uuid4(),
            max_turns=4,
            use_agent_runtime=True,
            arbitration_enabled=True,
            required_citation_ids=[citation_a, citation_b],
        )
    )

    assert result.turns_attempted == 2
    assert result.turns_created == 2
    assert result.stop_reason == "arbitration_requested"
    assert connection.inserted_event_types == [
        "turn.committed",
        "turn.committed",
        "turn.arbitration_requested",
        "conversation.paused",
    ]
    assert connection.updated_status == "paused"
