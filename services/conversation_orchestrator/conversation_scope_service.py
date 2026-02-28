"""Scope resolver for conversation tenant/workspace ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError


@dataclass(frozen=True)
class ConversationScope:
    tenant_id: UUID
    workspace_id: UUID


class ConversationScopeService:
    def __init__(self, connection: Any):
        self._connection = connection

    def get_scope(self, conversation_id: UUID) -> ConversationScope:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tenant_id, workspace_id
                FROM conversation
                WHERE id = %s
                """,
                (str(conversation_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise ConversationNotFoundError(f"Conversation {conversation_id} not found")

        return ConversationScope(tenant_id=row[0], workspace_id=row[1])
