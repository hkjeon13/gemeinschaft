"""Agent runtime wrapper with simple model routing and optional external LLM calls."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request
from uuid import UUID, uuid4


class UnknownAgentError(RuntimeError):
    """Raised when agent key is not configured."""


class RuntimeProviderConfigError(RuntimeError):
    """Raised when provider config is missing or invalid."""


class RuntimeProviderError(RuntimeError):
    """Raised when provider request/response fails."""


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


@dataclass(frozen=True)
class _ProviderRunResult:
    output_text: str
    token_in: int
    token_out: int
    finish_reason: str


@dataclass(frozen=True)
class _ResolvedTarget:
    provider: str
    model: str


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _normalize_provider(raw: str) -> str:
    normalized = raw.strip().lower()
    aliases = {
        "": "stub",
        "mock": "stub",
        "deterministic": "stub",
        "local": "stub",
        "gemini": "google",
    }
    return aliases.get(normalized, normalized)


def _parse_timeout_seconds() -> float:
    raw = os.getenv("AGENT_RUNTIME_TIMEOUT_SECONDS", "30")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise RuntimeProviderConfigError(
            "AGENT_RUNTIME_TIMEOUT_SECONDS must be a number"
        ) from exc
    if timeout <= 0:
        raise RuntimeProviderConfigError(
            "AGENT_RUNTIME_TIMEOUT_SECONDS must be > 0"
        )
    return timeout


def _extract_openai_text(choice: dict[str, Any]) -> str:
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
    return ""


def _split_requested_model(raw: str) -> tuple[str | None, str]:
    value = raw.strip()
    if not value:
        return None, value
    if ":" not in value:
        return None, value
    provider_raw, model = value.split(":", 1)
    normalized_provider = _normalize_provider(provider_raw)
    if normalized_provider in {"stub", "openai", "anthropic", "google"} and model.strip():
        return normalized_provider, model.strip()
    return None, value


class ModelRouter:
    def __init__(self):
        base_provider = _normalize_provider(os.getenv("AGENT_RUNTIME_PROVIDER", "stub"))
        self._defaults = {
            "ai_1": os.getenv("AGENT_AI_1_MODEL", "gpt-4o-mini"),
            "ai_2": os.getenv("AGENT_AI_2_MODEL", "gpt-4.1-mini"),
        }
        self._providers = {
            "ai_1": _normalize_provider(os.getenv("AGENT_AI_1_PROVIDER", base_provider)),
            "ai_2": _normalize_provider(os.getenv("AGENT_AI_2_PROVIDER", base_provider)),
        }
        self._fallback = os.getenv("AGENT_DEFAULT_MODEL", "gpt-4.1-mini")
        self._fallback_provider = _normalize_provider(
            os.getenv("AGENT_DEFAULT_PROVIDER", base_provider)
        )

    def resolve_model(self, agent_key: str, requested_model: str | None) -> str:
        return self.resolve_target(agent_key=agent_key, requested_model=requested_model).model

    def resolve_target(self, agent_key: str, requested_model: str | None) -> _ResolvedTarget:
        if agent_key not in self._defaults:
            raise UnknownAgentError(f"Unknown agent key: {agent_key}")
        if requested_model:
            requested_provider, requested_model_name = _split_requested_model(requested_model)
            provider = requested_provider or self._providers.get(agent_key, self._fallback_provider)
            return _ResolvedTarget(provider=provider, model=requested_model_name)
        return _ResolvedTarget(
            provider=self._providers.get(agent_key, self._fallback_provider),
            model=self._defaults.get(agent_key, self._fallback),
        )


class AgentRuntime:
    def __init__(self, router: ModelRouter):
        self._router = router
        self._timeout_seconds = _parse_timeout_seconds()

    def run_agent(self, payload: RunAgentInput) -> RunAgentResult:
        started = time.perf_counter()
        target = self._router.resolve_target(
            agent_key=payload.agent_key,
            requested_model=payload.requested_model,
        )
        seed_text = self._seed_text(payload)
        provider_result = self._run_provider(
            provider=target.provider,
            selected_model=target.model,
            payload=payload,
            seed_text=seed_text,
        )
        output_text = provider_result.output_text.strip() or f"[{payload.agent_key}]"
        token_in = provider_result.token_in or _estimate_tokens(seed_text)
        token_out = provider_result.token_out or min(
            payload.max_output_tokens,
            _estimate_tokens(output_text),
        )
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))

        return RunAgentResult(
            run_id=uuid4(),
            agent_key=payload.agent_key,
            selected_model=target.model,
            output_text=output_text,
            token_in=token_in,
            token_out=token_out,
            latency_ms=latency_ms,
            finish_reason=provider_result.finish_reason,
        )

    def _seed_text(self, payload: RunAgentInput) -> str:
        context_part = payload.context_text.strip()
        prompt_part = payload.prompt.strip()
        seed_text = f"{context_part}\n{prompt_part}".strip()
        if not seed_text:
            seed_text = "No context provided."
        return seed_text

    def _run_provider(
        self,
        *,
        provider: str,
        selected_model: str,
        payload: RunAgentInput,
        seed_text: str,
    ) -> _ProviderRunResult:
        normalized_provider = _normalize_provider(provider)
        if normalized_provider == "stub":
            return self._run_stub_provider(payload=payload, seed_text=seed_text)
        if normalized_provider == "openai":
            return self._run_openai_provider(
                selected_model=selected_model,
                payload=payload,
                seed_text=seed_text,
            )
        if normalized_provider == "anthropic":
            return self._run_anthropic_provider(
                selected_model=selected_model,
                payload=payload,
                seed_text=seed_text,
            )
        if normalized_provider == "google":
            return self._run_google_provider(
                selected_model=selected_model,
                payload=payload,
                seed_text=seed_text,
            )
        raise RuntimeProviderConfigError(
            "provider must be one of: stub, openai, anthropic, google"
        )

    def _run_stub_provider(
        self, *, payload: RunAgentInput, seed_text: str
    ) -> _ProviderRunResult:
        base = seed_text[: min(len(seed_text), max(40, payload.max_output_tokens))]
        output_text = f"[{payload.agent_key}] {base}"
        return _ProviderRunResult(
            output_text=output_text,
            token_in=_estimate_tokens(seed_text),
            token_out=min(payload.max_output_tokens, _estimate_tokens(output_text)),
            finish_reason="completed",
        )

    def _run_openai_provider(
        self,
        *,
        selected_model: str,
        payload: RunAgentInput,
        seed_text: str,
    ) -> _ProviderRunResult:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeProviderConfigError(
                "OPENAI_API_KEY is required when AGENT_RUNTIME_PROVIDER=openai"
            )
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        response = self._post_json(
            provider="openai",
            endpoint=f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": selected_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Return a concise turn response grounded in the prompt.",
                    },
                    {"role": "user", "content": seed_text},
                ],
                "max_tokens": payload.max_output_tokens,
            },
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeProviderError("openai response missing choices")
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        output_text = _extract_openai_text(first_choice)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        token_in = int(usage.get("prompt_tokens") or 0)
        token_out = int(usage.get("completion_tokens") or 0)
        finish_reason = str(first_choice.get("finish_reason") or "completed")
        return _ProviderRunResult(
            output_text=output_text,
            token_in=token_in,
            token_out=token_out,
            finish_reason=finish_reason,
        )

    def _run_anthropic_provider(
        self,
        *,
        selected_model: str,
        payload: RunAgentInput,
        seed_text: str,
    ) -> _ProviderRunResult:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeProviderConfigError(
                "ANTHROPIC_API_KEY is required when AGENT_RUNTIME_PROVIDER=anthropic"
            )
        base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip("/")
        anthropic_version = os.getenv("ANTHROPIC_VERSION", "2023-06-01")
        response = self._post_json(
            provider="anthropic",
            endpoint=f"{base_url}/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": anthropic_version,
                "Content-Type": "application/json",
            },
            payload={
                "model": selected_model,
                "max_tokens": payload.max_output_tokens,
                "messages": [{"role": "user", "content": seed_text}],
            },
        )
        content = response.get("content")
        if not isinstance(content, list):
            raise RuntimeProviderError("anthropic response missing content")
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        token_in = int(usage.get("input_tokens") or 0)
        token_out = int(usage.get("output_tokens") or 0)
        finish_reason = str(response.get("stop_reason") or "completed")
        return _ProviderRunResult(
            output_text="\n".join(text_parts).strip(),
            token_in=token_in,
            token_out=token_out,
            finish_reason=finish_reason,
        )

    def _run_google_provider(
        self,
        *,
        selected_model: str,
        payload: RunAgentInput,
        seed_text: str,
    ) -> _ProviderRunResult:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeProviderConfigError(
                "GOOGLE_API_KEY is required when AGENT_RUNTIME_PROVIDER=google"
            )
        base_url = os.getenv(
            "GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        model_name = (
            selected_model.split("/", 1)[1]
            if selected_model.startswith("models/")
            else selected_model
        )
        endpoint = (
            f"{base_url}/v1beta/models/{model_name}:generateContent"
            f"?key={parse.quote_plus(api_key)}"
        )
        response = self._post_json(
            provider="google",
            endpoint=endpoint,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"role": "user", "parts": [{"text": seed_text}]}],
                "generationConfig": {"maxOutputTokens": payload.max_output_tokens},
            },
        )
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeProviderError("google response missing candidates")
        first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content = (
            first_candidate.get("content")
            if isinstance(first_candidate.get("content"), dict)
            else {}
        )
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        text_parts: list[str] = []
        for item in parts:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        usage = (
            response.get("usageMetadata")
            if isinstance(response.get("usageMetadata"), dict)
            else {}
        )
        token_in = int(usage.get("promptTokenCount") or 0)
        token_out = int(usage.get("candidatesTokenCount") or 0)
        finish_reason = str(first_candidate.get("finishReason") or "completed")
        return _ProviderRunResult(
            output_text="\n".join(text_parts).strip(),
            token_in=token_in,
            token_out=token_out,
            finish_reason=finish_reason,
        )

    def _post_json(
        self,
        *,
        provider: str,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=endpoint,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:  # nosec B310
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeProviderError(
                f"{provider} HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeProviderError(f"{provider} request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeProviderError(f"{provider} request timed out") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeProviderError(
                f"{provider} response is not valid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeProviderError(f"{provider} response must be a JSON object")
        return parsed
