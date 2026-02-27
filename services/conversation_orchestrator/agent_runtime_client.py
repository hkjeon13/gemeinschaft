"""HTTP client for Agent Runtime service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class AgentRuntimeCallError(RuntimeError):
    """Raised when Agent Runtime service call fails."""


@dataclass(frozen=True)
class RunAgentClientInput:
    agent_key: str
    prompt: str
    context_packet: dict[str, Any]
    max_output_tokens: int
    requested_model: str | None = None


@dataclass(frozen=True)
class RunAgentClientResult:
    run_id: str
    agent_key: str
    selected_model: str
    output_text: str
    token_in: int
    token_out: int
    latency_ms: int
    finish_reason: str


class AgentRuntimeClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def run_agent(self, payload: RunAgentClientInput) -> RunAgentClientResult:
        endpoint = f"{self._base_url}/internal/agents/run"
        body = json.dumps(
            {
                "agent_key": payload.agent_key,
                "prompt": payload.prompt,
                "context_packet": payload.context_packet,
                "max_output_tokens": payload.max_output_tokens,
                "requested_model": payload.requested_model,
            }
        ).encode("utf-8")
        req = request.Request(
            url=endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:  # nosec B310
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise AgentRuntimeCallError(
                f"Agent runtime HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except error.URLError as exc:
            raise AgentRuntimeCallError(f"Agent runtime request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AgentRuntimeCallError("Agent runtime request timed out") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentRuntimeCallError("Agent runtime returned invalid JSON") from exc

        try:
            return RunAgentClientResult(
                run_id=str(parsed["run_id"]),
                agent_key=str(parsed["agent_key"]),
                selected_model=str(parsed["selected_model"]),
                output_text=str(parsed["output_text"]),
                token_in=int(parsed["token_in"]),
                token_out=int(parsed["token_out"]),
                latency_ms=int(parsed["latency_ms"]),
                finish_reason=str(parsed["finish_reason"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise AgentRuntimeCallError(
                "Agent runtime response missing required fields"
            ) from exc
