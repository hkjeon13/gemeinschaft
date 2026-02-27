"""Agent runtime wrapper with simple model routing."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from uuid import UUID, uuid4


class UnknownAgentError(RuntimeError):
    """Raised when agent key is not configured."""


@dataclass(frozen=True)
class RunAgentInput:
    agent_key: str
    prompt: str
    context_text: str
    max_output_tokens: int
    requested_model: str | None = None


@dataclass(frozen=True)
class RunAgentResult:
    run_id: UUID
    agent_key: str
    selected_model: str
    output_text: str
    token_in: int
    token_out: int
    latency_ms: int
    finish_reason: str


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


class ModelRouter:
    def __init__(self):
        self._defaults = {
            "ai_1": os.getenv("AGENT_AI_1_MODEL", "gpt-4o-mini"),
            "ai_2": os.getenv("AGENT_AI_2_MODEL", "gpt-4.1-mini"),
        }
        self._fallback = os.getenv("AGENT_DEFAULT_MODEL", "gpt-4.1-mini")

    def resolve_model(self, agent_key: str, requested_model: str | None) -> str:
        if agent_key not in self._defaults:
            raise UnknownAgentError(f"Unknown agent key: {agent_key}")
        if requested_model:
            return requested_model
        return self._defaults.get(agent_key, self._fallback)


class AgentRuntime:
    def __init__(self, router: ModelRouter):
        self._router = router

    def run_agent(self, payload: RunAgentInput) -> RunAgentResult:
        started = time.perf_counter()
        selected_model = self._router.resolve_model(
            agent_key=payload.agent_key,
            requested_model=payload.requested_model,
        )

        context_part = payload.context_text.strip()
        prompt_part = payload.prompt.strip()
        seed_text = f"{context_part}\n{prompt_part}".strip()
        if not seed_text:
            seed_text = "No context provided."
        base = seed_text[: min(len(seed_text), max(40, payload.max_output_tokens))]
        output_text = f"[{payload.agent_key}] {base}"
        token_in = _estimate_tokens(seed_text)
        token_out = min(payload.max_output_tokens, _estimate_tokens(output_text))
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))

        return RunAgentResult(
            run_id=uuid4(),
            agent_key=payload.agent_key,
            selected_model=selected_model,
            output_text=output_text,
            token_in=token_in,
            token_out=token_out,
            latency_ms=latency_ms,
            finish_reason="completed",
        )
