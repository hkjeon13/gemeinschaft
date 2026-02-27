"""Unit tests for Agent Runtime HTTP client."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib import error

import pytest

from services.conversation_orchestrator.agent_runtime_client import (
    AgentRuntimeCallError,
    AgentRuntimeClient,
    RunAgentClientInput,
)


class _DummyResponse:
    def __init__(self, payload: str):
        self._payload = payload

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


def test_agent_runtime_client_success(monkeypatch: Any) -> None:
    payload = {
        "run_id": "run-1",
        "agent_key": "ai_1",
        "selected_model": "model-a",
        "output_text": "ok",
        "token_in": 10,
        "token_out": 20,
        "latency_ms": 30,
        "finish_reason": "completed",
    }

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = AgentRuntimeClient("http://agent-runtime:8002")

    result = client.run_agent(
        RunAgentClientInput(
            agent_key="ai_1",
            prompt="test",
            context_packet={"a": 1},
            max_output_tokens=120,
        )
    )

    assert result.run_id == "run-1"
    assert result.output_text == "ok"
    assert result.selected_model == "model-a"


def test_agent_runtime_client_http_error(monkeypatch: Any) -> None:
    http_error = error.HTTPError(
        url="http://agent-runtime:8002/internal/agents/run",
        code=500,
        msg="Internal Server Error",
        hdrs=None,
        fp=io.BytesIO(b"boom"),
    )

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        raise http_error

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = AgentRuntimeClient("http://agent-runtime:8002")

    with pytest.raises(AgentRuntimeCallError):
        client.run_agent(
            RunAgentClientInput(
                agent_key="ai_1",
                prompt="test",
                context_packet={},
                max_output_tokens=120,
            )
        )


def test_agent_runtime_client_invalid_json(monkeypatch: Any) -> None:
    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse("not-json")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = AgentRuntimeClient("http://agent-runtime:8002")

    with pytest.raises(AgentRuntimeCallError):
        client.run_agent(
            RunAgentClientInput(
                agent_key="ai_1",
                prompt="test",
                context_packet={},
                max_output_tokens=120,
            )
        )
