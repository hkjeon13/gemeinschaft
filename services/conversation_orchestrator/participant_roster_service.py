"""Read-model service for conversation participant roster."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class ParticipantRosterRecord:
    participant_id: UUID
    kind: str
    display_name: str
    role_label: str | None
    joined_at: datetime
    left_at: datetime | None
    muted: bool
    metadata: dict[str, Any]


class ParticipantRosterService:
    def __init__(self, connection: Any):
        self._connection = connection

    def list_participants(
        self,
        *,
        conversation_id: UUID,
        include_left: bool = False,
        limit: int = 100,
        after_joined_at: datetime | None = None,
        after_participant_id: UUID | None = None,
    ) -> list[ParticipantRosterRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if (after_joined_at is None) != (after_participant_id is None):
            raise ValueError("after_joined_at and after_participant_id must be provided together")
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
                raise ConversationNotFoundError(f"Conversation {conversation_id} not found")

            if include_left:
                if after_joined_at is None:
                    cursor.execute(
                        """
                        SELECT
                            p.id,
                            p.kind,
                            p.display_name,
                            p.role_label,
                            p.joined_at,
                            p.left_at,
                            (
                                COALESCE(p.metadata #>> '{moderation,muted}', 'false') = 'true'
                            ) AS muted,
                            p.metadata
                        FROM participant p
                        WHERE p.conversation_id = %s
                        ORDER BY p.joined_at ASC, p.id ASC
                        LIMIT %s
                        """,
                        (str(conversation_id), limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT
                            p.id,
                            p.kind,
                            p.display_name,
                            p.role_label,
                            p.joined_at,
                            p.left_at,
                            (
                                COALESCE(p.metadata #>> '{moderation,muted}', 'false') = 'true'
                            ) AS muted,
                            p.metadata
                        FROM participant p
                        WHERE
                            p.conversation_id = %s
                            AND (
                                p.joined_at > %s
                                OR (p.joined_at = %s AND p.id > %s)
                            )
                        ORDER BY p.joined_at ASC, p.id ASC
                        LIMIT %s
                        """,
                        (
                            str(conversation_id),
                            after_joined_at,
                            after_joined_at,
                            str(after_participant_id),
                            limit,
                        ),
                    )
            else:
                if after_joined_at is None:
                    cursor.execute(
                        """
                        SELECT
                            p.id,
                            p.kind,
                            p.display_name,
                            p.role_label,
                            p.joined_at,
                            p.left_at,
                            (
                                COALESCE(p.metadata #>> '{moderation,muted}', 'false') = 'true'
                            ) AS muted,
                            p.metadata
                        FROM participant p
                        WHERE p.conversation_id = %s AND p.left_at IS NULL
                        ORDER BY p.joined_at ASC, p.id ASC
                        LIMIT %s
                        """,
                        (str(conversation_id), limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT
                            p.id,
                            p.kind,
                            p.display_name,
                            p.role_label,
                            p.joined_at,
                            p.left_at,
                            (
                                COALESCE(p.metadata #>> '{moderation,muted}', 'false') = 'true'
                            ) AS muted,
                            p.metadata
                        FROM participant p
                        WHERE
                            p.conversation_id = %s
                            AND p.left_at IS NULL
                            AND (
                                p.joined_at > %s
                                OR (p.joined_at = %s AND p.id > %s)
                            )
                        ORDER BY p.joined_at ASC, p.id ASC
                        LIMIT %s
                        """,
                        (
                            str(conversation_id),
                            after_joined_at,
                            after_joined_at,
                            str(after_participant_id),
                            limit,
                        ),
                    )
            rows = cursor.fetchall()

        return [
            ParticipantRosterRecord(
                participant_id=row[0],
                kind=row[1],
                display_name=row[2],
                role_label=row[3],
                joined_at=row[4],
                left_at=row[5],
                muted=bool(row[6]),
                metadata=row[7],
            )
            for row in rows
        ]
