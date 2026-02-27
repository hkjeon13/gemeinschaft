"""Conversation start service for automation/manual triggers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ParticipantSeed:
    kind: str
    display_name: str
    role_label: str | None = None
    user_id: UUID | None = None
    agent_profile_id: UUID | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class StartConversationInput:
    tenant_id: UUID
    workspace_id: UUID
    title: str
    objective: str
    start_trigger: str
    metadata: dict[str, Any]
    participants: list[ParticipantSeed]
    automation_template_id: UUID | None = None
    automation_run_id: str | None = None
    scheduled_for: datetime | None = None
    initiated_by_user_id: UUID | None = None


@dataclass(frozen=True)
class StartConversationResult:
    conversation_id: UUID
    status: str
    start_trigger: str
    created: bool
    event_seq_last: int
    created_at: datetime
    started_at: datetime | None


class ConversationStartService:
    def __init__(self, connection: Any):
        self._connection = connection

    def start_conversation(self, payload: StartConversationInput) -> StartConversationResult:
        if payload.start_trigger not in {"automation", "human"}:
            raise ValueError("start_trigger must be 'automation' or 'human'")

        existing = self._find_existing_automation_conversation(payload)
        if existing is not None:
            return existing

        metadata = dict(payload.metadata)
        if payload.automation_template_id is not None:
            metadata["automation_template_id"] = str(payload.automation_template_id)
        if payload.automation_run_id is not None:
            metadata["automation_run_id"] = payload.automation_run_id
        if payload.scheduled_for is not None:
            metadata["scheduled_for"] = payload.scheduled_for.isoformat()
        if payload.initiated_by_user_id is not None:
            metadata["initiated_by_user_id"] = str(payload.initiated_by_user_id)

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation (
                        tenant_id,
                        workspace_id,
                        title,
                        objective,
                        status,
                        start_trigger,
                        started_at,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, 'active', %s, NOW(), %s::jsonb)
                    RETURNING id, status, start_trigger, created_at, started_at
                    """,
                    (
                        str(payload.tenant_id),
                        str(payload.workspace_id),
                        payload.title,
                        payload.objective,
                        payload.start_trigger,
                        json.dumps(metadata),
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("Conversation insert did not return a row")

                conversation_id = row[0]
                created_at = row[3]
                started_at = row[4]
                for participant in payload.participants:
                    cursor.execute(
                        """
                        INSERT INTO participant (
                            conversation_id,
                            kind,
                            user_id,
                            agent_profile_id,
                            display_name,
                            role_label,
                            metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            str(conversation_id),
                            participant.kind,
                            str(participant.user_id) if participant.user_id else None,
                            str(participant.agent_profile_id)
                            if participant.agent_profile_id
                            else None,
                            participant.display_name,
                            participant.role_label,
                            json.dumps(participant.metadata or {}),
                        ),
                    )

                event_payload_created = {"trigger": payload.start_trigger}
                event_payload_started = {
                    "mode": "default" if payload.start_trigger == "automation" else "manual"
                }
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, 1, 'conversation.created', %s::jsonb)
                    """,
                    (str(conversation_id), json.dumps(event_payload_created)),
                )
                cursor.execute(
                    """
                    INSERT INTO event (
                        conversation_id,
                        seq_no,
                        event_type,
                        payload
                    )
                    VALUES (%s, 2, 'conversation.started', %s::jsonb)
                    """,
                    (str(conversation_id), json.dumps(event_payload_started)),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return StartConversationResult(
            conversation_id=conversation_id,
            status=row[1],
            start_trigger=row[2],
            created=True,
            event_seq_last=2,
            created_at=created_at,
            started_at=started_at,
        )

    def _find_existing_automation_conversation(
        self, payload: StartConversationInput
    ) -> StartConversationResult | None:
        if payload.start_trigger != "automation" or not payload.automation_run_id:
            return None

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, start_trigger, created_at, started_at
                FROM conversation
                WHERE
                    tenant_id = %s
                    AND workspace_id = %s
                    AND metadata ->> 'automation_run_id' = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    str(payload.tenant_id),
                    str(payload.workspace_id),
                    payload.automation_run_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            conversation_id = row[0]
            cursor.execute(
                """
                SELECT COALESCE(MAX(seq_no), 0)
                FROM event
                WHERE conversation_id = %s
                """,
                (str(conversation_id),),
            )
            seq_row = cursor.fetchone()
            event_seq_last = int(seq_row[0]) if seq_row else 0

        return StartConversationResult(
            conversation_id=conversation_id,
            status=row[1],
            start_trigger=row[2],
            created=False,
            event_seq_last=event_seq_last,
            created_at=row[3],
            started_at=row[4],
        )
