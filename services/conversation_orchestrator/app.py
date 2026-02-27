"""Conversation orchestrator app."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from services.conversation_orchestrator.context_packet_builder import (
    ContextPacketBuilder,
    ContextPacketInput,
    ContextPacketResult,
    TopicNotFoundError,
)
from services.conversation_orchestrator.conversation_start_service import (
    ConversationStartService,
    ParticipantSeed,
    StartConversationInput,
    StartConversationResult,
)
from services.conversation_orchestrator.event_store import (
    AppendEventInput,
    AppendEventResult,
    ConversationNotFoundError,
    EventStore,
    SequenceConflictError,
)
from services.conversation_orchestrator.loop_runner import (
    ConversationLoopRunner,
    NoParticipantsError,
    RunLoopInput,
    RunLoopResult,
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


class ParticipantSeedRequest(BaseModel):
    kind: str
    display_name: str = Field(min_length=1)
    role_label: str | None = None
    user_id: UUID | None = None
    agent_profile_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class StartAutomationConversationRequest(BaseModel):
    tenant_id: UUID
    workspace_id: UUID
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    automation_template_id: UUID | None = None
    automation_run_id: str | None = None
    scheduled_for: datetime | None = None
    participants: list[ParticipantSeedRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class StartManualConversationRequest(BaseModel):
    tenant_id: UUID
    workspace_id: UUID
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    initiated_by_user_id: UUID | None = None
    participants: list[ParticipantSeedRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class StartConversationResponse(BaseModel):
    conversation_id: UUID
    status: str
    start_trigger: str
    created: bool
    event_seq_last: int
    created_at: datetime
    started_at: datetime | None


class RunLoopRequest(BaseModel):
    max_turns: int = Field(ge=1, le=100)
    require_citations: bool = False
    required_citation_ids: list[UUID] = Field(default_factory=list, max_length=50)

    model_config = ConfigDict(extra="forbid")


class RunLoopResponse(BaseModel):
    conversation_id: UUID
    turns_created: int
    turns_rejected: int
    event_seq_last: int
    turn_index_last: int
    started_at: datetime
    finished_at: datetime


class AssembleContextRequest(BaseModel):
    source_document_id: UUID
    topic_id: UUID | None = None
    turn_window: int = Field(default=8, ge=1, le=50)
    evidence_limit: int = Field(default=5, ge=1, le=20)

    model_config = ConfigDict(extra="forbid")


class ContextTurnResponse(BaseModel):
    turn_index: int
    speaker: str
    content_text: str


class ContextEvidenceResponse(BaseModel):
    source_chunk_id: UUID
    chunk_index: int
    content_text: str
    relevance_score: float


class AssembleContextResponse(BaseModel):
    conversation_id: UUID
    source_document_id: UUID
    topic_id: UUID | None
    topic_label: str | None
    topic_summary: str | None
    recent_turns: list[ContextTurnResponse]
    evidence_chunks: list[ContextEvidenceResponse]


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


def _build_conversation_start_service(connection: Any) -> ConversationStartService:
    return ConversationStartService(connection)


def _build_loop_runner(connection: Any) -> ConversationLoopRunner:
    return ConversationLoopRunner(connection)


def _build_context_packet_builder(connection: Any) -> ContextPacketBuilder:
    return ContextPacketBuilder(connection)


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


def _participant_seeds(participants: list[ParticipantSeedRequest]) -> list[ParticipantSeed]:
    return [
        ParticipantSeed(
            kind=participant.kind,
            display_name=participant.display_name,
            role_label=participant.role_label,
            user_id=participant.user_id,
            agent_profile_id=participant.agent_profile_id,
            metadata=participant.metadata,
        )
        for participant in participants
    ]


@app.post(
    "/internal/conversations/start/automation",
    response_model=StartConversationResponse,
    status_code=201,
)
def start_automation_conversation(
    request: StartAutomationConversationRequest, response: Response
) -> StartConversationResponse:
    connection = _connect()
    service = _build_conversation_start_service(connection)
    try:
        result: StartConversationResult = service.start_conversation(
            StartConversationInput(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                title=request.title,
                objective=request.objective,
                start_trigger="automation",
                metadata=request.metadata,
                participants=_participant_seeds(request.participants),
                automation_template_id=request.automation_template_id,
                automation_run_id=request.automation_run_id,
                scheduled_for=request.scheduled_for,
            )
        )
    finally:
        connection.close()

    if not result.created:
        response.status_code = 200
    return StartConversationResponse(
        conversation_id=result.conversation_id,
        status=result.status,
        start_trigger=result.start_trigger,
        created=result.created,
        event_seq_last=result.event_seq_last,
        created_at=result.created_at,
        started_at=result.started_at,
    )


@app.post(
    "/internal/conversations/start/manual",
    response_model=StartConversationResponse,
    status_code=201,
)
def start_manual_conversation(
    request: StartManualConversationRequest,
) -> StartConversationResponse:
    connection = _connect()
    service = _build_conversation_start_service(connection)
    try:
        result: StartConversationResult = service.start_conversation(
            StartConversationInput(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                title=request.title,
                objective=request.objective,
                start_trigger="human",
                metadata=request.metadata,
                participants=_participant_seeds(request.participants),
                initiated_by_user_id=request.initiated_by_user_id,
            )
        )
    finally:
        connection.close()

    return StartConversationResponse(
        conversation_id=result.conversation_id,
        status=result.status,
        start_trigger=result.start_trigger,
        created=result.created,
        event_seq_last=result.event_seq_last,
        created_at=result.created_at,
        started_at=result.started_at,
    )


@app.post(
    "/internal/conversations/{conversation_id}/loop/run",
    response_model=RunLoopResponse,
)
def run_conversation_loop(
    conversation_id: UUID, request: RunLoopRequest
) -> RunLoopResponse:
    connection = _connect()
    runner = _build_loop_runner(connection)
    try:
        result: RunLoopResult = runner.run_loop(
            RunLoopInput(
                conversation_id=conversation_id,
                max_turns=request.max_turns,
                require_citations=request.require_citations,
                required_citation_ids=request.required_citation_ids,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoParticipantsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return RunLoopResponse(
        conversation_id=result.conversation_id,
        turns_created=result.turns_created,
        turns_rejected=result.turns_rejected,
        event_seq_last=result.event_seq_last,
        turn_index_last=result.turn_index_last,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


@app.post(
    "/internal/conversations/{conversation_id}/context/assemble",
    response_model=AssembleContextResponse,
)
def assemble_context_packet(
    conversation_id: UUID, request: AssembleContextRequest
) -> AssembleContextResponse:
    connection = _connect()
    builder = _build_context_packet_builder(connection)
    try:
        result: ContextPacketResult = builder.build_packet(
            ContextPacketInput(
                conversation_id=conversation_id,
                source_document_id=request.source_document_id,
                topic_id=request.topic_id,
                turn_window=request.turn_window,
                evidence_limit=request.evidence_limit,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TopicNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return AssembleContextResponse(
        conversation_id=result.conversation_id,
        source_document_id=result.source_document_id,
        topic_id=result.topic_id,
        topic_label=result.topic_label,
        topic_summary=result.topic_summary,
        recent_turns=[
            ContextTurnResponse(
                turn_index=turn.turn_index,
                speaker=turn.speaker,
                content_text=turn.content_text,
            )
            for turn in result.recent_turns
        ],
        evidence_chunks=[
            ContextEvidenceResponse(
                source_chunk_id=evidence.source_chunk_id,
                chunk_index=evidence.chunk_index,
                content_text=evidence.content_text,
                relevance_score=evidence.relevance_score,
            )
            for evidence in result.evidence_chunks
        ],
    )
