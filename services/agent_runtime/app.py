"""Agent runtime service app."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.agent_runtime.runtime import (
    AgentRuntime,
    ModelRouter,
    RunAgentInput,
    RunAgentResult,
    UnknownAgentError,
)
from services.shared.app_factory import build_service_app

app = build_service_app("agent_runtime")


class RunAgentRequest(BaseModel):
    agent_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    context_packet: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int = Field(default=256, ge=1, le=4096)
    requested_model: str | None = None

    model_config = ConfigDict(extra="forbid")


class RunAgentResponse(BaseModel):
    run_id: str
    agent_key: str
    selected_model: str
    output_text: str
    token_in: int
    token_out: int
    latency_ms: int
    finish_reason: str


def _build_runtime() -> AgentRuntime:
    return AgentRuntime(router=ModelRouter())


def _flatten_context(context_packet: dict[str, Any]) -> str:
    if not context_packet:
        return ""
    parts: list[str] = []
    for key in sorted(context_packet):
        value = context_packet[key]
        parts.append(f"{key}: {value}")
    return "\n".join(parts)


@app.post("/internal/agents/run", response_model=RunAgentResponse)
def run_agent(request: RunAgentRequest) -> RunAgentResponse:
    runtime = _build_runtime()
    try:
        result: RunAgentResult = runtime.run_agent(
            RunAgentInput(
                agent_key=request.agent_key,
                prompt=request.prompt,
                context_text=_flatten_context(request.context_packet),
                max_output_tokens=request.max_output_tokens,
                requested_model=request.requested_model,
            )
        )
    except UnknownAgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RunAgentResponse(
        run_id=str(result.run_id),
        agent_key=result.agent_key,
        selected_model=result.selected_model,
        output_text=result.output_text,
        token_in=result.token_in,
        token_out=result.token_out,
        latency_ms=result.latency_ms,
        finish_reason=result.finish_reason,
    )
