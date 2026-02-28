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


class AutomationRunNotFoundError(RuntimeError):
    """Raised when automation run cannot be found."""


@dataclass(frozen=True)
class AutomationTemplateRecord:
    template_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    conversation_objective: str
    participants: list[str]
    enabled: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AutomationTemplateListRecord:
    template_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    rrule: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AutomationTemplateDetailRecord:
    template_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    conversation_objective: str
    rrule: str
    participants: list[str]
    enabled: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


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


@dataclass(frozen=True)
class AutomationRunRecord:
    run_id: int
    template_id: UUID
    scheduled_for: datetime
    idempotency_key: str
    status: str
    triggered_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SetTemplateEnabledResult:
    template_id: UUID
    enabled: bool
    updated_at: datetime


@dataclass(frozen=True)
class UpdateAutomationTemplateInput:
    template_id: UUID
    name: str | None = None
    conversation_objective: str | None = None
    rrule: str | None = None
    participants: list[str] | None = None
    metadata: dict[str, Any] | None = None


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

    def get_template(self, template_id: UUID) -> AutomationTemplateRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    tenant_id,
                    workspace_id,
                    name,
                    conversation_objective,
                    participants,
                    enabled,
                    metadata
                FROM automation_template
                WHERE id = %s
                """,
                (str(template_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise TemplateNotFoundError(
                    f"Automation template {template_id} not found or disabled"
                )
            return AutomationTemplateRecord(
                template_id=row[0],
                tenant_id=row[1],
                workspace_id=row[2],
                name=row[3],
                conversation_objective=row[4],
                participants=row[5] if isinstance(row[5], list) else [],
                enabled=bool(row[6]),
                metadata=row[7] if isinstance(row[7], dict) else {},
            )

    def list_runs(
        self,
        *,
        template_id: UUID,
        limit: int = 20,
        before_scheduled_for: datetime | None = None,
        before_run_id: int | None = None,
    ) -> list[AutomationRunRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if (before_scheduled_for is None) != (before_run_id is None):
            raise ValueError(
                "before_scheduled_for and before_run_id must be provided together"
            )
        with self._connection.cursor() as cursor:
            params: list[Any] = [str(template_id)]
            where_clause = "template_id = %s"
            if before_scheduled_for is not None and before_run_id is not None:
                where_clause += (
                    " AND (scheduled_for < %s OR (scheduled_for = %s AND id < %s))"
                )
                params.extend([before_scheduled_for, before_scheduled_for, before_run_id])
            cursor.execute(
                f"""
                SELECT
                    id,
                    template_id,
                    scheduled_for,
                    idempotency_key,
                    status,
                    triggered_at,
                    metadata
                FROM automation_run
                WHERE {where_clause}
                ORDER BY scheduled_for DESC, id DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cursor.fetchall()

        return [
            AutomationRunRecord(
                run_id=int(row[0]),
                template_id=row[1],
                scheduled_for=row[2],
                idempotency_key=row[3],
                status=row[4],
                triggered_at=row[5],
                metadata=row[6] if isinstance(row[6], dict) else {},
            )
            for row in rows
        ]

    def get_run(self, run_id: int) -> AutomationRunRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    template_id,
                    scheduled_for,
                    idempotency_key,
                    status,
                    triggered_at,
                    metadata
                FROM automation_run
                WHERE id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationRunNotFoundError(f"Automation run {run_id} not found")

        return AutomationRunRecord(
            run_id=int(row[0]),
            template_id=row[1],
            scheduled_for=row[2],
            idempotency_key=row[3],
            status=row[4],
            triggered_at=row[5],
            metadata=row[6] if isinstance(row[6], dict) else {},
        )

    def list_templates(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        include_disabled: bool = False,
        limit: int = 50,
        before_updated_at: datetime | None = None,
        before_template_id: UUID | None = None,
    ) -> list[AutomationTemplateListRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if (before_updated_at is None) != (before_template_id is None):
            raise ValueError(
                "before_updated_at and before_template_id must be provided together"
            )
        with self._connection.cursor() as cursor:
            where_clauses = ["tenant_id = %s", "workspace_id = %s"]
            params: list[Any] = [str(tenant_id), str(workspace_id)]
            if not include_disabled:
                where_clauses.append("enabled = TRUE")
            if before_updated_at is not None and before_template_id is not None:
                where_clauses.append(
                    "(updated_at < %s OR (updated_at = %s AND id < %s))"
                )
                params.extend([before_updated_at, before_updated_at, str(before_template_id)])

            cursor.execute(
                f"""
                SELECT
                    id,
                    tenant_id,
                    workspace_id,
                    name,
                    rrule,
                    enabled,
                    created_at,
                    updated_at
                FROM automation_template
                WHERE {" AND ".join(where_clauses)}
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cursor.fetchall()

        return [
            AutomationTemplateListRecord(
                template_id=row[0],
                tenant_id=row[1],
                workspace_id=row[2],
                name=row[3],
                rrule=row[4],
                enabled=bool(row[5]),
                created_at=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]

    def get_template_detail(self, template_id: UUID) -> AutomationTemplateDetailRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    tenant_id,
                    workspace_id,
                    name,
                    conversation_objective,
                    rrule,
                    participants,
                    enabled,
                    metadata,
                    created_at,
                    updated_at
                FROM automation_template
                WHERE id = %s
                """,
                (str(template_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise TemplateNotFoundError(
                    f"Automation template {template_id} not found or disabled"
                )

        return AutomationTemplateDetailRecord(
            template_id=row[0],
            tenant_id=row[1],
            workspace_id=row[2],
            name=row[3],
            conversation_objective=row[4],
            rrule=row[5],
            participants=row[6] if isinstance(row[6], list) else [],
            enabled=bool(row[7]),
            metadata=row[8] if isinstance(row[8], dict) else {},
            created_at=row[9],
            updated_at=row[10],
        )

    def set_template_enabled(
        self, *, template_id: UUID, enabled: bool
    ) -> SetTemplateEnabledResult:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE automation_template
                    SET
                        enabled = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, enabled, updated_at
                    """,
                    (enabled, str(template_id)),
                )
                row = cursor.fetchone()
                if row is None:
                    raise TemplateNotFoundError(
                        f"Automation template {template_id} not found or disabled"
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return SetTemplateEnabledResult(
            template_id=row[0],
            enabled=bool(row[1]),
            updated_at=row[2],
        )

    def update_template(
        self,
        payload: UpdateAutomationTemplateInput,
    ) -> AutomationTemplateDetailRecord:
        if (
            payload.name is None
            and payload.conversation_objective is None
            and payload.rrule is None
            and payload.participants is None
            and payload.metadata is None
        ):
            raise ValueError("at least one update field must be provided")

        serialized_participants = (
            json.dumps(payload.participants) if payload.participants is not None else None
        )
        serialized_metadata = (
            json.dumps(payload.metadata) if payload.metadata is not None else None
        )

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE automation_template
                    SET
                        name = COALESCE(%s, name),
                        conversation_objective = COALESCE(%s, conversation_objective),
                        rrule = COALESCE(%s, rrule),
                        participants = COALESCE(%s::jsonb, participants),
                        metadata = COALESCE(%s::jsonb, metadata),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING
                        id,
                        tenant_id,
                        workspace_id,
                        name,
                        conversation_objective,
                        rrule,
                        participants,
                        enabled,
                        metadata,
                        created_at,
                        updated_at
                    """,
                    (
                        payload.name,
                        payload.conversation_objective,
                        payload.rrule,
                        serialized_participants,
                        serialized_metadata,
                        str(payload.template_id),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise TemplateNotFoundError(
                        f"Automation template {payload.template_id} not found or disabled"
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return AutomationTemplateDetailRecord(
            template_id=row[0],
            tenant_id=row[1],
            workspace_id=row[2],
            name=row[3],
            conversation_objective=row[4],
            rrule=row[5],
            participants=row[6] if isinstance(row[6], list) else [],
            enabled=bool(row[7]),
            metadata=row[8] if isinstance(row[8], dict) else {},
            created_at=row[9],
            updated_at=row[10],
        )

    def mark_run_failed(
        self,
        *,
        run_id: int,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        run_metadata = metadata or {}
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE automation_run
                    SET
                        status = 'failed',
                        error_message = %s,
                        metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                    WHERE id = %s
                    RETURNING id
                    """,
                    (error_message, json.dumps(run_metadata), run_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AutomationRunNotFoundError(
                        f"Automation run {run_id} not found"
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
