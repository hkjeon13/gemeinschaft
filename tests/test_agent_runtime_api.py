"""API tests for agent runtime wrapper."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from services.agent_runtime import app as agent_runtime_app_module


class _DummyResponse:
    def __init__(self, payload: str):
        self._payload = payload

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


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


def test_run_agent_endpoint_rejects_viewer_role() -> None:
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        headers={"x-internal-role": "viewer"},
        json={
            "agent_key": "ai_1",
            "prompt": "test",
        },
    )

    assert response.status_code == 403


def test_run_agent_endpoint_openai_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "openai output"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6},
                }
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_1",
            "prompt": "hello",
            "context_packet": {"topic": "refund"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == "openai output"
    assert payload["token_in"] == 12
    assert payload["token_out"] == 6
    assert payload["finish_reason"] == "stop"


def test_run_agent_endpoint_anthropic_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse(
            json.dumps(
                {
                    "content": [{"type": "text", "text": "anthropic output"}],
                    "usage": {"input_tokens": 4, "output_tokens": 3},
                    "stop_reason": "end_turn",
                }
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_2",
            "prompt": "hello",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == "anthropic output"
    assert payload["token_in"] == 4
    assert payload["token_out"] == 3
    assert payload["finish_reason"] == "end_turn"


def test_run_agent_endpoint_google_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse(
            json.dumps(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "google output"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 9,
                        "candidatesTokenCount": 7,
                    },
                }
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_1",
            "prompt": "hello",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == "google output"
    assert payload["token_in"] == 9
    assert payload["token_out"] == 7
    assert payload["finish_reason"] == "STOP"


def test_run_agent_endpoint_provider_missing_api_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_1",
            "prompt": "hello",
        },
    )

    assert response.status_code == 500
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_run_agent_endpoint_supports_per_agent_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_AI_1_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_AI_2_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        url = str(getattr(req, "full_url", ""))
        if "/chat/completions" in url:
            return _DummyResponse(
                json.dumps(
                    {
                        "choices": [
                            {"message": {"content": "from openai"}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                )
            )
        if "/messages" in url:
            return _DummyResponse(
                json.dumps(
                    {
                        "content": [{"type": "text", "text": "from anthropic"}],
                        "usage": {"input_tokens": 5, "output_tokens": 4},
                        "stop_reason": "end_turn",
                    }
                )
            )
        raise AssertionError(f"Unexpected provider URL: {url}")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = TestClient(agent_runtime_app_module.app)

    response_ai1 = client.post(
        "/internal/agents/run",
        json={"agent_key": "ai_1", "prompt": "hello"},
    )
    response_ai2 = client.post(
        "/internal/agents/run",
        json={"agent_key": "ai_2", "prompt": "hello"},
    )

    assert response_ai1.status_code == 200
    assert response_ai1.json()["output_text"] == "from openai"
    assert response_ai2.status_code == 200
    assert response_ai2.json()["output_text"] == "from anthropic"


def test_run_agent_endpoint_requested_model_can_override_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_AI_1_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    seen_urls: list[str] = []

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        url = str(getattr(req, "full_url", ""))
        seen_urls.append(url)
        if ":generateContent" in url:
            return _DummyResponse(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "content": {"parts": [{"text": "from google"}]},
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
                    }
                )
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = TestClient(agent_runtime_app_module.app)

    response = client.post(
        "/internal/agents/run",
        json={
            "agent_key": "ai_1",
            "prompt": "hello",
            "requested_model": "google:gemini-2.0-flash",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_model"] == "gemini-2.0-flash"
    assert payload["output_text"] == "from google"
    assert any("models/gemini-2.0-flash:generateContent" in url for url in seen_urls)
