"""API tests for agent runtime wrapper."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from services.agent_runtime import app as agent_runtime_app_module


def test_run_agent_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_AI_1_MODEL", "model-a")
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_1",
            "prompt": "Summarize this topic",
            "context_packet": {"topic": "refund policy", "depth": "short"},
            "max_output_tokens": 120,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_key"] == "ai_1"
    assert payload["selected_model"] == "model-a"
    assert payload["output_text"]
    assert payload["finish_reason"] == "completed"


def test_run_agent_endpoint_invalid_agent() -> None:
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_99",
            "prompt": "test",
        },
    )

    assert response.status_code == 400
    assert "unknown agent key" in response.json()["detail"].lower()


def test_run_agent_endpoint_uses_requested_model() -> None:
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_2",
            "prompt": "test",
            "requested_model": "custom-model",
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_model"] == "custom-model"
