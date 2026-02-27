"""API tests for conversation loop run endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.loop_runner import (
    ConversationNotActiveError,
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
            turns_created=payload.max_turns,
            turns_rejected=0,
            event_seq_last=9,
            turn_index_last=7,
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
    assert payload["turns_created"] == 3
    assert payload["turns_rejected"] == 0
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
