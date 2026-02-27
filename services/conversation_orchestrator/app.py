"""Conversation orchestrator app."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.conversation_orchestrator.event_store import (
    AppendEventInput,
    AppendEventResult,
    ConversationNotFoundError,
    EventStore,
    SequenceConflictError,
)
from services.conversation_orchestrator.snapshot_projector import (
    ConversationSnapshot,
    SnapshotProjector,
)
from services.shared.app_factory import build_service_app

app = build_service_app("conversation_orchestrator")


class AppendEventRequest(BaseModel):
    conversation_id: UUID
    event_type: str = Field(min_length=1)
    expected_seq_no: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    message_id: UUID | None = None
    actor_participant_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class AppendEventResponse(BaseModel):
    event_id: int
    seq_no: int
    created_at: datetime


class ConversationSnapshotResponse(BaseModel):
    conversation_id: UUID
    status: str
    last_seq_no: int
    turn_count: int
    started_at: datetime | None
    ended_at: datetime | None
    last_event_at: datetime | None


def _connect() -> Any:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(
            status_code=500,
            detail="DATABASE_URL is not configured",
        )
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        raise HTTPException(
            status_code=500,
            detail="psycopg is not installed",
        ) from exc
    return psycopg.connect(database_url)


@app.post("/internal/events/append", response_model=AppendEventResponse, status_code=201)
def append_event(request: AppendEventRequest) -> AppendEventResponse:
    connection = _connect()
    store = EventStore(connection)
    try:
        result: AppendEventResult = store.append_event(
            AppendEventInput(
                conversation_id=request.conversation_id,
                event_type=request.event_type,
                expected_seq_no=request.expected_seq_no,
                payload=request.payload,
                message_id=request.message_id,
                actor_participant_id=request.actor_participant_id,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SequenceConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_seq_no": exc.expected_seq_no,
                "actual_seq_no": exc.actual_seq_no,
            },
        ) from exc
    finally:
        connection.close()

    return AppendEventResponse(
        event_id=result.event_id,
        seq_no=result.seq_no,
        created_at=result.created_at,
    )


@app.post(
    "/internal/snapshots/rebuild/{conversation_id}",
    response_model=ConversationSnapshotResponse,
)
def rebuild_conversation_snapshot(conversation_id: UUID) -> ConversationSnapshotResponse:
    connection = _connect()
    projector = SnapshotProjector(connection)
    try:
        snapshot: ConversationSnapshot = projector.rebuild_conversation_snapshot(
            conversation_id=conversation_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ConversationSnapshotResponse(
        conversation_id=snapshot.conversation_id,
        status=snapshot.status,
        last_seq_no=snapshot.last_seq_no,
        turn_count=snapshot.turn_count,
        started_at=snapshot.started_at,
        ended_at=snapshot.ended_at,
        last_event_at=snapshot.last_event_at,
    )
