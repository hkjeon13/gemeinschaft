"""DB repository for scheduler automation templates and runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


class TemplateNotFoundError(RuntimeError):
    """Raised when automation template cannot be found/enabled."""


@dataclass(frozen=True)
class CreateAutomationTemplateInput:
    tenant_id: UUID
    workspace_id: UUID
    name: str
    conversation_objective: str
    rrule: str
    participants: list[str]
    enabled: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CreateAutomationTemplateResult:
    template_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class TriggerAutomationRunInput:
    template_id: UUID
    scheduled_for: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TriggerAutomationRunResult:
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime


def normalize_scheduled_for(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc)
    return normalized.replace(second=0, microsecond=0)


def build_idempotency_key(template_id: UUID, scheduled_for: datetime) -> str:
    normalized = normalize_scheduled_for(scheduled_for)
    raw = f"{template_id}:{normalized.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SchedulerRepository:
    def __init__(self, connection: Any):
        self._connection = connection

    def create_template(
        self, payload: CreateAutomationTemplateInput
    ) -> CreateAutomationTemplateResult:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO automation_template (
                        tenant_id,
                        workspace_id,
                        name,
                        conversation_objective,
                        rrule,
                        participants,
                        enabled,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                    RETURNING id, created_at
                    """,
                    (
                        str(payload.tenant_id),
                        str(payload.workspace_id),
                        payload.name,
                        payload.conversation_objective,
                        payload.rrule,
                        json.dumps(payload.participants),
                        payload.enabled,
                        json.dumps(payload.metadata),
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("Template insert did not return a row")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return CreateAutomationTemplateResult(template_id=row[0], created_at=row[1])

    def trigger_run(self, payload: TriggerAutomationRunInput) -> TriggerAutomationRunResult:
        scheduled_for = normalize_scheduled_for(payload.scheduled_for)
        idempotency_key = build_idempotency_key(payload.template_id, scheduled_for)

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM automation_template
                    WHERE id = %s AND enabled = TRUE
                    """,
                    (str(payload.template_id),),
                )
                if cursor.fetchone() is None:
                    raise TemplateNotFoundError(
                        f"Automation template {payload.template_id} not found or disabled"
                    )

                cursor.execute(
                    """
                    INSERT INTO automation_run (
                        template_id,
                        scheduled_for,
                        idempotency_key,
                        status,
                        metadata
                    )
                    VALUES (%s, %s, %s, 'triggered', %s::jsonb)
                    ON CONFLICT (template_id, idempotency_key) DO NOTHING
                    RETURNING id, status, triggered_at
                    """,
                    (
                        str(payload.template_id),
                        scheduled_for,
                        idempotency_key,
                        json.dumps(payload.metadata),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """
                        SELECT id, status, triggered_at
                        FROM automation_run
                        WHERE template_id = %s AND idempotency_key = %s
                        """,
                        (str(payload.template_id), idempotency_key),
                    )
                    existing = cursor.fetchone()
                    if existing is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("Idempotent run check failed")
                    run_id = int(existing[0])
                    triggered_at = existing[2]
                    status = "duplicate"
                else:
                    run_id = int(row[0])
                    triggered_at = row[2]
                    status = row[1]
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return TriggerAutomationRunResult(
            run_id=run_id,
            template_id=payload.template_id,
            scheduled_for=scheduled_for,
            idempotency_key=idempotency_key,
            status=status,
            triggered_at=triggered_at,
        )
