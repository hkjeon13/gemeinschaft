"""HTTP client for Conversation Orchestrator start endpoints."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, request
from uuid import UUID


class OrchestratorCallError(RuntimeError):
    """Raised when orchestrator call fails."""


@dataclass(frozen=True)
class StartAutomationConversationClientInput:
    tenant_id: UUID
    workspace_id: UUID
    title: str
    objective: str
    automation_template_id: UUID
    automation_run_id: str
    scheduled_for: datetime
    participants: list[dict[str, Any]]
    metadata: dict[str, Any]
    request_id: str | None = None


@dataclass(frozen=True)
class StartAutomationConversationClientResult:
    conversation_id: UUID
    status: str
    start_trigger: str
    created: bool
    event_seq_last: int


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        *,
        internal_api_token: str | None = None,
        role: str = "system",
        principal_id: str | None = None,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.2,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._internal_api_token = (
            internal_api_token.strip() if internal_api_token else None
        )
        normalized_role = role.strip().lower() if role else "system"
        self._role = normalized_role or "system"
        self._principal_id = principal_id.strip() if principal_id else None
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    def _build_headers(
        self, payload: StartAutomationConversationClientInput
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-internal-role": self._role,
            "x-auth-tenant-id": str(payload.tenant_id),
            "x-auth-workspace-id": str(payload.workspace_id),
        }
        if self._internal_api_token:
            headers["x-internal-api-token"] = self._internal_api_token
        if self._principal_id:
            headers["x-internal-principal-id"] = self._principal_id
        if payload.request_id:
            headers["x-request-id"] = payload.request_id
        return headers

    def start_automation_conversation(
        self, payload: StartAutomationConversationClientInput
    ) -> StartAutomationConversationClientResult:
        endpoint = f"{self._base_url}/internal/conversations/start/automation"
        body = json.dumps(
            {
                "tenant_id": str(payload.tenant_id),
                "workspace_id": str(payload.workspace_id),
                "title": payload.title,
                "objective": payload.objective,
                "automation_template_id": str(payload.automation_template_id),
                "automation_run_id": payload.automation_run_id,
                "scheduled_for": payload.scheduled_for.isoformat(),
                "participants": payload.participants,
                "metadata": payload.metadata,
            }
        ).encode("utf-8")
        req = request.Request(
            url=endpoint,
            data=body,
            method="POST",
            headers=self._build_headers(payload),
        )
        attempt = 0
        while True:
            try:
                with request.urlopen(req, timeout=self._timeout_seconds) as response:  # nosec B310
                    raw = response.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                should_retry = 500 <= exc.code < 600 and attempt < self._max_retries
                if should_retry:
                    self._sleep_before_retry(attempt)
                    attempt += 1
                    continue
                detail = exc.read().decode("utf-8", errors="ignore")
                raise OrchestratorCallError(
                    f"Orchestrator HTTP {exc.code}: {detail or exc.reason}"
                ) from exc
            except error.URLError as exc:
                if attempt < self._max_retries:
                    self._sleep_before_retry(attempt)
                    attempt += 1
                    continue
                raise OrchestratorCallError(
                    f"Orchestrator request failed: {exc.reason}"
                ) from exc
            except TimeoutError as exc:
                if attempt < self._max_retries:
                    self._sleep_before_retry(attempt)
                    attempt += 1
                    continue
                raise OrchestratorCallError("Orchestrator request timed out") from exc

        try:
            parsed = json.loads(raw)
            conversation_id = UUID(str(parsed["conversation_id"]))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise OrchestratorCallError("Orchestrator returned invalid response") from exc

        return StartAutomationConversationClientResult(
            conversation_id=conversation_id,
            status=str(parsed.get("status", "")),
            start_trigger=str(parsed.get("start_trigger", "")),
            created=bool(parsed.get("created", False)),
            event_seq_last=int(parsed.get("event_seq_last", 0)),
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._retry_backoff_seconds * (2**attempt)
        if delay > 0:
            time.sleep(delay)
