"""API tests for conversation loop run endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.agent_runtime_client import AgentRuntimeCallError
from services.conversation_orchestrator.context_packet_builder import TopicNotFoundError
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.loop_runner import (
    AgentRuntimeNotConfiguredError,
    ConversationNotActiveError,
    ContextBuilderNotConfiguredError,
    NoParticipantsError,
    RunLoopResult,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        return RunLoopResult(
            conversation_id=payload.conversation_id,
            turns_attempted=payload.max_turns,
            turns_created=payload.max_turns,
            turns_pending_approval=0,
            turns_rejected=0,
            event_seq_last=9,
            turn_index_last=7,
            stop_reason=None,
            started_at=datetime(2026, 2, 27, 20, 10, tzinfo=timezone.utc),
            finished_at=datetime(2026, 2, 27, 20, 10, 1, tzinfo=timezone.utc),
        )


class NotFoundLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class NoParticipantsLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise NoParticipantsError(f"Conversation {payload.conversation_id} has no participants")


class NotActiveLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise ConversationNotActiveError(
            f"Conversation {payload.conversation_id} is not active (status=paused)"
        )


class TopicMissingLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise TopicNotFoundError("Topic not found")


class RuntimeMissingLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise AgentRuntimeNotConfiguredError("AGENT_RUNTIME_BASE_URL is not configured")


class RuntimeFailureLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise AgentRuntimeCallError("Agent runtime HTTP 500")


class ContextBuilderMissingLoopRunner:
    def run_loop(self, payload: Any) -> RunLoopResult:
        raise ContextBuilderNotConfiguredError("context builder not configured")


def test_run_loop_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: SuccessLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/loop/run",
        json={"max_turns": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["turns_attempted"] == 3
    assert payload["turns_created"] == 3
    assert payload["turns_pending_approval"] == 0
    assert payload["turns_rejected"] == 0
    assert payload["stop_reason"] is None
    assert payload["event_seq_last"] == 9
    assert payload["turn_index_last"] == 7


def test_run_loop_endpoint_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: NotFoundLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/loop/run",
        json={"max_turns": 2},
    )

    assert response.status_code == 404


def test_run_loop_endpoint_no_participants(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: NoParticipantsLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/loop/run",
        json={"max_turns": 2},
    )

    assert response.status_code == 400


def test_run_loop_endpoint_non_active(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: NotActiveLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/loop/run",
        json={"max_turns": 2},
    )

    assert response.status_code == 409


def test_run_loop_endpoint_topic_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: TopicMissingLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    response = client.post(
        f"/internal/conversations/{uuid4()}/loop/run",
        json={"max_turns": 1, "source_document_id": str(uuid4()), "topic_id": str(uuid4())},
    )

    assert response.status_code == 404


def test_run_loop_endpoint_runtime_not_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: RuntimeMissingLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    response = client.post(
        f"/internal/conversations/{uuid4()}/loop/run",
        json={"max_turns": 1, "use_agent_runtime": True},
    )

    assert response.status_code == 500


def test_run_loop_endpoint_runtime_call_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: RuntimeFailureLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    response = client.post(
        f"/internal/conversations/{uuid4()}/loop/run",
        json={"max_turns": 1, "use_agent_runtime": True},
    )

    assert response.status_code == 502


def test_run_loop_endpoint_context_builder_missing(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_loop_runner",
        lambda connection: ContextBuilderMissingLoopRunner(),
    )
    client = TestClient(orchestrator_app_module.app)
    response = client.post(
        f"/internal/conversations/{uuid4()}/loop/run",
        json={"max_turns": 1, "source_document_id": str(uuid4())},
    )

    assert response.status_code == 500
