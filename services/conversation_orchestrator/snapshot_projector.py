"""Conversation snapshot projector based on replaying immutable events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError

VALID_STATUSES = {
    "draft",
    "prepared",
    "active",
    "paused",
    "completed",
    "curated",
    "versioned",
    "archived",
}

STATUS_BY_EVENT_TYPE = {
    "conversation.created": "draft",
    "conversation.prepared": "prepared",
    "conversation.started": "active",
    "conversation.paused": "paused",
    "conversation.resumed": "active",
    "conversation.completed": "completed",
    "conversation.terminated": "completed",
    "conversation.curated": "curated",
    "conversation.versioned": "versioned",
    "conversation.archived": "archived",
}


@dataclass(frozen=True)
class ProjectableEvent:
    seq_no: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass
class ConversationSnapshot:
    conversation_id: UUID
    status: str = "draft"
    last_seq_no: int = 0
    turn_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_event_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _normalize_status_from_event(event: ProjectableEvent) -> str | None:
    status_from_payload = event.payload.get("status")
    if isinstance(status_from_payload, str) and status_from_payload in VALID_STATUSES:
        return status_from_payload
    return STATUS_BY_EVENT_TYPE.get(event.event_type)


def _apply_event(snapshot: ConversationSnapshot, event: ProjectableEvent) -> None:
    if event.seq_no <= snapshot.last_seq_no:
        raise ValueError(
            f"Events must be strictly increasing by seq_no; got {event.seq_no} "
            f"after {snapshot.last_seq_no}"
        )

    snapshot.last_seq_no = event.seq_no
    snapshot.last_event_at = event.created_at

    if event.event_type == "turn.committed":
        snapshot.turn_count += 1

    status = _normalize_status_from_event(event)
    if status is not None:
        snapshot.status = status

    if event.event_type == "conversation.started" and snapshot.started_at is None:
        snapshot.started_at = event.created_at

    if event.event_type in {"conversation.completed", "conversation.terminated"}:
        if snapshot.ended_at is None:
            snapshot.ended_at = event.created_at


def project_snapshot(
    conversation_id: UUID, events: list[ProjectableEvent]
) -> ConversationSnapshot:
    snapshot = ConversationSnapshot(conversation_id=conversation_id)
    for event in events:
        _apply_event(snapshot, event)
    return snapshot


class SnapshotProjector:
    """Rebuilds and persists conversation snapshot rows from event replay."""

    def __init__(self, connection: Any):
        self._connection = connection

    def rebuild_conversation_snapshot(self, conversation_id: UUID) -> ConversationSnapshot:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM conversation
                    WHERE id = %s
                    """,
                    (str(conversation_id),),
                )
                if cursor.fetchone() is None:
                    raise ConversationNotFoundError(
                        f"Conversation {conversation_id} not found"
                    )

                cursor.execute(
                    """
                    SELECT seq_no, event_type, payload, created_at
                    FROM event
                    WHERE conversation_id = %s
                    ORDER BY seq_no ASC
                    """,
                    (str(conversation_id),),
                )
                rows = cursor.fetchall()
                events = [
                    ProjectableEvent(
                        seq_no=int(row[0]),
                        event_type=row[1],
                        payload=row[2] if row[2] is not None else {},
                        created_at=row[3],
                    )
                    for row in rows
                ]
                snapshot = project_snapshot(conversation_id=conversation_id, events=events)

                cursor.execute(
                    """
                    INSERT INTO conversation_snapshot (
                        conversation_id,
                        status,
                        last_seq_no,
                        turn_count,
                        started_at,
                        ended_at,
                        last_event_at,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (conversation_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        last_seq_no = EXCLUDED.last_seq_no,
                        turn_count = EXCLUDED.turn_count,
                        started_at = EXCLUDED.started_at,
                        ended_at = EXCLUDED.ended_at,
                        last_event_at = EXCLUDED.last_event_at,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        str(snapshot.conversation_id),
                        snapshot.status,
                        snapshot.last_seq_no,
                        snapshot.turn_count,
                        snapshot.started_at,
                        snapshot.ended_at,
                        snapshot.last_event_at,
                        json.dumps(snapshot.metadata),
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return snapshot
