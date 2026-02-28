"""Unit tests for scheduler orchestrator client."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any
from urllib import error
from uuid import uuid4

import pytest

from services.scheduler.orchestrator_client import (
    OrchestratorCallError,
    OrchestratorClient,
    StartAutomationConversationClientInput,
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


def _input_payload(
    *, request_id: str | None = None
) -> StartAutomationConversationClientInput:
    return StartAutomationConversationClientInput(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        title="Hourly default",
        objective="Generate periodic insights",
        automation_template_id=uuid4(),
        automation_run_id="11",
        scheduled_for=datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc),
        participants=[{"kind": "ai", "display_name": "AI(1)"}],
        metadata={"source": "test"},
        request_id=request_id,
    )


def test_orchestrator_client_start_success(monkeypatch: Any) -> None:
    payload = {
        "conversation_id": str(uuid4()),
        "status": "active",
        "start_trigger": "automation",
        "created": True,
        "event_seq_last": 2,
    }
    captured: dict[str, Any] = {}

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        captured["timeout"] = timeout
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {key.lower(): value for key, value in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _DummyResponse(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient(
        "http://orchestrator:8001",
        internal_api_token="top-secret",
        role="operator",
        principal_id="scheduler-service",
    )

    input_payload = _input_payload(request_id="req-123")
    result = client.start_automation_conversation(input_payload)

    assert result.status == "active"
    assert result.start_trigger == "automation"
    assert result.created is True
    assert captured["timeout"] == 10.0
    assert (
        captured["url"]
        == "http://orchestrator:8001/internal/conversations/start/automation"
    )
    assert captured["method"] == "POST"
    headers = captured["headers"]
    assert headers["x-internal-api-token"] == "top-secret"
    assert headers["x-internal-role"] == "operator"
    assert headers["x-internal-principal-id"] == "scheduler-service"
    assert headers["x-auth-tenant-id"] == str(input_payload.tenant_id)
    assert headers["x-auth-workspace-id"] == str(input_payload.workspace_id)
    assert headers["x-request-id"] == "req-123"
    assert captured["body"]["title"] == "Hourly default"


def test_orchestrator_client_http_error(monkeypatch: Any) -> None:
    http_error = error.HTTPError(
        url="http://orchestrator:8001/internal/conversations/start/automation",
        code=500,
        msg="Internal Server Error",
        hdrs=None,
        fp=io.BytesIO(b"boom"),
    )

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        raise http_error

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient("http://orchestrator:8001")

    with pytest.raises(OrchestratorCallError):
        client.start_automation_conversation(_input_payload())


def test_orchestrator_client_retries_url_error_then_success(monkeypatch: Any) -> None:
    payload = {
        "conversation_id": str(uuid4()),
        "status": "active",
        "start_trigger": "automation",
        "created": True,
        "event_seq_last": 2,
    }
    attempts = {"count": 0}

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        del req, timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise error.URLError("temporary network issue")
        return _DummyResponse(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient(
        "http://orchestrator:8001",
        max_retries=1,
        retry_backoff_seconds=0.0,
    )

    result = client.start_automation_conversation(_input_payload())

    assert result.status == "active"
    assert attempts["count"] == 2


def test_orchestrator_client_no_retry_on_http_400(monkeypatch: Any) -> None:
    http_error = error.HTTPError(
        url="http://orchestrator:8001/internal/conversations/start/automation",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(b"bad"),
    )
    attempts = {"count": 0}

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        del req, timeout
        attempts["count"] += 1
        raise http_error

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient(
        "http://orchestrator:8001",
        max_retries=3,
        retry_backoff_seconds=0.0,
    )

    with pytest.raises(OrchestratorCallError):
        client.start_automation_conversation(_input_payload())
    assert attempts["count"] == 1


def test_orchestrator_client_retries_http_500_until_exhausted(monkeypatch: Any) -> None:
    attempts = {"count": 0}

    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        del req, timeout
        attempts["count"] += 1
        raise error.HTTPError(
            url="http://orchestrator:8001/internal/conversations/start/automation",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b"boom"),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient(
        "http://orchestrator:8001",
        max_retries=2,
        retry_backoff_seconds=0.0,
    )

    with pytest.raises(OrchestratorCallError):
        client.start_automation_conversation(_input_payload())
    assert attempts["count"] == 3


def test_orchestrator_client_invalid_json(monkeypatch: Any) -> None:
    def _fake_urlopen(req: Any, timeout: float) -> _DummyResponse:
        return _DummyResponse("not-json")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    client = OrchestratorClient("http://orchestrator:8001")

    with pytest.raises(OrchestratorCallError):
        client.start_automation_conversation(_input_payload())
