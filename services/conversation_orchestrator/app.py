"""Conversation orchestrator app."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from services.conversation_orchestrator.agent_runtime_client import (
    AgentRuntimeCallError,
    AgentRuntimeClient,
)
from services.conversation_orchestrator.batch_turn_approval_service import (
    BatchTurnApprovalInput,
    BatchTurnApprovalItemInput,
    BatchTurnApprovalResult,
    BatchTurnApprovalService,
)
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
from services.conversation_orchestrator.conversation_scope_service import (
    ConversationScopeService,
)
from services.conversation_orchestrator.event_store import (
    AppendEventInput,
    AppendEventResult,
    ConversationNotFoundError,
    EventStore,
    SequenceConflictError,
)
from services.conversation_orchestrator.event_history_service import (
    ConversationEventRecord,
    EventHistoryService,
)
from services.conversation_orchestrator.event_export_service import EventExportService
from services.conversation_orchestrator.failure_summary_service import (
    ConversationFailureSummary,
    ConversationFailureSummaryService,
)
from services.conversation_orchestrator.intervention_service import (
    ApplyInterventionInput,
    ApplyInterventionResult,
    HumanInterventionService,
    InvalidInterventionStateError,
    InvalidInterventionTypeError,
)
from services.conversation_orchestrator.message_export_service import MessageExportService
from services.conversation_orchestrator.message_history_service import (
    ConversationMessageRecord,
    MessageHistoryService,
)
from services.conversation_orchestrator.loop_runner import (
    AgentRuntimeNotConfiguredError,
    ConversationNotActiveError,
    ConversationLoopRunner,
    ContextBuilderNotConfiguredError,
    NoParticipantsError,
    RunLoopInput,
    RunLoopResult,
)
from services.conversation_orchestrator.ops_summary_service import (
    ConversationOpsSummary,
    ConversationOpsSummaryService,
)
from services.conversation_orchestrator.participant_moderation_service import (
    ApplyParticipantModerationInput,
    ApplyParticipantModerationResult,
    InvalidModerationActionError,
    ParticipantModerationNotFoundError,
    ParticipantModerationService,
    ParticipantModerationStateError,
)
from services.conversation_orchestrator.participant_role_service import (
    ParticipantNotFoundError,
    ParticipantRoleService,
    SwitchParticipantRoleInput,
    SwitchParticipantRoleResult,
)
from services.conversation_orchestrator.participant_roster_service import (
    ParticipantRosterRecord,
    ParticipantRosterService,
)
from services.conversation_orchestrator.pending_turn_service import (
    PendingTurnRecord,
    PendingTurnService,
)
from services.conversation_orchestrator.rejected_turn_service import (
    RejectedTurnRecord,
    RejectedTurnService,
)
from services.conversation_orchestrator.snapshot_projector import (
    ConversationSnapshot,
    SnapshotProjector,
)
from services.conversation_orchestrator.turn_approval_service import (
    ApplyTurnApprovalInput,
    ApplyTurnApprovalResult,
    InvalidApprovalDecisionError,
    TurnApprovalService,
    TurnApprovalStateError,
    TurnNotFoundError,
)
from services.shared.app_factory import build_service_app
from services.shared.auth import enforce_role, enforce_scope, get_auth_context
from services.shared.db import get_db_connection

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
    source_document_id: UUID | None = None
    topic_id: UUID | None = None
    context_turn_window: int = Field(default=8, ge=1, le=50)
    context_evidence_limit: int = Field(default=5, ge=1, le=20)
    use_agent_runtime: bool = False
    agent_max_output_tokens: int = Field(default=256, ge=1, le=4096)
    require_human_approval: bool = False
    max_consecutive_rejections: int = Field(default=3, ge=1, le=20)
    arbitration_enabled: bool = False
    pause_on_disagreement: bool = True
    derailment_guard_enabled: bool = False
    min_topic_keyword_matches: int = Field(default=1, ge=1, le=10)

    model_config = ConfigDict(extra="forbid")


class RunLoopResponse(BaseModel):
    conversation_id: UUID
    turns_attempted: int
    turns_created: int
    turns_pending_approval: int
    turns_rejected: int
    event_seq_last: int
    turn_index_last: int
    stop_reason: str | None
    started_at: datetime
    finished_at: datetime


class ApplyInterventionRequest(BaseModel):
    intervention_type: str = Field(min_length=1)
    actor_participant_id: UUID | None = None
    instruction: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ApplyInterventionResponse(BaseModel):
    conversation_id: UUID
    status: str
    event_seq_last: int
    applied_events: list[str]
    occurred_at: datetime


class TurnApprovalRequest(BaseModel):
    decision: str = Field(min_length=1)
    actor_participant_id: UUID | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class TurnApprovalResponse(BaseModel):
    conversation_id: UUID
    turn_index: int
    message_status: str
    event_seq_last: int
    applied_events: list[str]
    occurred_at: datetime


class BatchTurnApprovalItemRequest(BaseModel):
    turn_index: int = Field(ge=1)
    decision: str = Field(min_length=1)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class BatchTurnApprovalRequest(BaseModel):
    actor_participant_id: UUID | None = None
    stop_on_error: bool = False
    decisions: list[BatchTurnApprovalItemRequest] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class BatchTurnApprovalItemResponse(BaseModel):
    turn_index: int
    success: bool
    message_status: str | None
    event_seq_last: int | None
    applied_events: list[str]
    error_code: str | None
    error_message: str | None


class BatchTurnApprovalResponse(BaseModel):
    conversation_id: UUID
    processed: int
    approved: int
    rejected: int
    failed: int
    results: list[BatchTurnApprovalItemResponse]


class PendingTurnResponse(BaseModel):
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    content_text: str
    created_at: datetime
    metadata: dict[str, Any]


class PendingTurnPageResponse(BaseModel):
    items: list[PendingTurnResponse]
    next_cursor: str | None
    has_more: bool


class ConversationEventResponse(BaseModel):
    seq_no: int
    event_type: str
    actor_participant_id: UUID | None
    message_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime


class ConversationMessageResponse(BaseModel):
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    status: str
    message_type: str
    content_text: str
    metadata: dict[str, Any]
    created_at: datetime


class ConversationEventPageResponse(BaseModel):
    items: list[ConversationEventResponse]
    next_cursor: str | None
    has_more: bool


class ConversationMessagePageResponse(BaseModel):
    items: list[ConversationMessageResponse]
    next_cursor: str | None
    has_more: bool


class RejectedTurnResponse(BaseModel):
    turn_index: int
    message_id: UUID
    participant_id: UUID
    participant_name: str
    participant_kind: str
    content_text: str
    failure_type: str | None
    reasons: list[str]
    created_at: datetime
    metadata: dict[str, Any]


class RejectedTurnPageResponse(BaseModel):
    items: list[RejectedTurnResponse]
    next_cursor: str | None
    has_more: bool


class ParticipantRosterResponse(BaseModel):
    participant_id: UUID
    kind: str
    display_name: str
    role_label: str | None
    joined_at: datetime
    left_at: datetime | None
    muted: bool
    metadata: dict[str, Any]


class ParticipantRosterPageResponse(BaseModel):
    items: list[ParticipantRosterResponse]
    next_cursor: str | None
    has_more: bool


class ConversationOpsSummaryResponse(BaseModel):
    conversation_id: UUID
    status: str
    title: str
    objective: str | None
    updated_at: datetime
    participant_count: int
    total_messages: int
    committed_messages: int
    proposed_messages: int
    rejected_messages: int
    validated_messages: int
    last_event_seq_no: int
    last_event_type: str | None
    last_event_at: datetime | None


class ConversationFailureSummaryResponse(BaseModel):
    conversation_id: UUID
    rejected_turns: int
    missing_citation_count: int
    invalid_citation_count: int
    loop_risk_repetition_count: int
    topic_derailment_count: int
    loop_guard_trigger_count: int
    arbitration_requested_count: int


class SwitchParticipantRoleRequest(BaseModel):
    new_role_label: str = Field(min_length=1)
    actor_participant_id: UUID | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class SwitchParticipantRoleResponse(BaseModel):
    conversation_id: UUID
    participant_id: UUID
    previous_role_label: str | None
    new_role_label: str
    event_seq_last: int
    occurred_at: datetime


class ApplyParticipantModerationRequest(BaseModel):
    action: str = Field(min_length=1)
    actor_participant_id: UUID | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ApplyParticipantModerationResponse(BaseModel):
    conversation_id: UUID
    participant_id: UUID
    muted: bool
    event_type: str
    event_seq_last: int
    occurred_at: datetime


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
    try:
        return get_db_connection("conversation_orchestrator")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_conversation_start_service(connection: Any) -> ConversationStartService:
    return ConversationStartService(connection)


def _build_conversation_scope_service(connection: Any) -> ConversationScopeService:
    return ConversationScopeService(connection)


def _build_loop_runner(connection: Any) -> ConversationLoopRunner:
    context_builder = ContextPacketBuilder(connection)
    agent_runtime_base_url = os.getenv("AGENT_RUNTIME_BASE_URL")
    runtime_client = (
        AgentRuntimeClient(agent_runtime_base_url) if agent_runtime_base_url else None
    )
    return ConversationLoopRunner(
        connection,
        context_builder=context_builder,
        agent_runtime_client=runtime_client,
    )


def _build_intervention_service(connection: Any) -> HumanInterventionService:
    return HumanInterventionService(connection)


def _build_turn_approval_service(connection: Any) -> TurnApprovalService:
    return TurnApprovalService(connection)


def _build_batch_turn_approval_service(connection: Any) -> BatchTurnApprovalService:
    return BatchTurnApprovalService(_build_turn_approval_service(connection))


def _build_pending_turn_service(connection: Any) -> PendingTurnService:
    return PendingTurnService(connection)


def _build_event_history_service(connection: Any) -> EventHistoryService:
    return EventHistoryService(connection)


def _build_message_history_service(connection: Any) -> MessageHistoryService:
    return MessageHistoryService(connection)


def _build_message_export_service(connection: Any) -> MessageExportService:
    return MessageExportService(connection)


def _build_event_export_service(connection: Any) -> EventExportService:
    return EventExportService(connection)


def _build_rejected_turn_service(connection: Any) -> RejectedTurnService:
    return RejectedTurnService(connection)


def _build_participant_role_service(connection: Any) -> ParticipantRoleService:
    return ParticipantRoleService(connection)


def _build_participant_roster_service(connection: Any) -> ParticipantRosterService:
    return ParticipantRosterService(connection)


def _build_participant_moderation_service(connection: Any) -> ParticipantModerationService:
    return ParticipantModerationService(connection)


def _build_ops_summary_service(connection: Any) -> ConversationOpsSummaryService:
    return ConversationOpsSummaryService(connection)


def _build_failure_summary_service(connection: Any) -> ConversationFailureSummaryService:
    return ConversationFailureSummaryService(connection)


def _build_context_packet_builder(connection: Any) -> ContextPacketBuilder:
    return ContextPacketBuilder(connection)


def _authorize(
    request: Request,
    *,
    allowed_roles: set[str] | None = None,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    auth = get_auth_context(request)
    if allowed_roles is not None:
        enforce_role(auth, allowed_roles=allowed_roles)
    enforce_scope(auth, tenant_id=tenant_id, workspace_id=workspace_id)


def _authorize_conversation(
    request: Request,
    *,
    conversation_id: UUID,
    allowed_roles: set[str] | None = None,
) -> None:
    _authorize(request, allowed_roles=allowed_roles)
    auth = get_auth_context(request)
    if auth.tenant_id is None and auth.workspace_id is None:
        return

    connection = _connect()
    scope_service = _build_conversation_scope_service(connection)
    try:
        scope = scope_service.get_scope(conversation_id=conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()
    enforce_scope(
        auth,
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
    )


def _parse_cursor(*, cursor: str | None, prefix: str) -> int:
    if cursor is None or not cursor.strip():
        return 0
    token = cursor.strip()
    expected_prefix = f"{prefix}:"
    if not token.startswith(expected_prefix):
        raise HTTPException(
            status_code=400,
            detail=f"cursor must start with '{expected_prefix}'",
        )
    raw_value = token[len(expected_prefix) :]
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor index must be an integer") from exc
    if value < 0:
        raise HTTPException(status_code=400, detail="cursor index must be >= 0")
    return value


def _build_cursor(*, prefix: str, value: int) -> str:
    return f"{prefix}:{value}"


def _resolve_after_cursor(
    *,
    cursor: str | None,
    prefix: str,
    explicit_after: int,
    explicit_name: str,
) -> int:
    if cursor is None or not cursor.strip():
        return explicit_after
    cursor_after = _parse_cursor(cursor=cursor, prefix=prefix)
    if explicit_after != 0 and explicit_after != cursor_after:
        raise HTTPException(
            status_code=400,
            detail=f"{explicit_name} conflicts with cursor",
        )
    return cursor_after


def _parse_participant_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None or not cursor.strip():
        return None, None
    token = cursor.strip()
    prefix = "p:"
    if not token.startswith(prefix):
        raise HTTPException(status_code=400, detail="cursor must start with 'p:'")
    parts = token[len(prefix) :].split("|", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="cursor must be '<joined_at>|<id>'")
    joined_at_raw, participant_id_raw = parts[0], parts[1]
    try:
        joined_at = datetime.fromisoformat(joined_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor joined_at is invalid") from exc
    if joined_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="cursor joined_at must include timezone")
    try:
        participant_id = UUID(participant_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor participant_id is invalid") from exc
    return joined_at, participant_id


def _build_participant_cursor(record: ParticipantRosterRecord) -> str:
    joined_at_utc = record.joined_at.astimezone(timezone.utc)
    token = joined_at_utc.isoformat().replace("+00:00", "Z")
    return f"p:{token}|{record.participant_id}"


def _to_event_response(record: ConversationEventRecord) -> ConversationEventResponse:
    return ConversationEventResponse(
        seq_no=record.seq_no,
        event_type=record.event_type,
        actor_participant_id=record.actor_participant_id,
        message_id=record.message_id,
        payload=record.payload,
        created_at=record.created_at,
    )


def _to_message_response(row: ConversationMessageRecord) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        turn_index=row.turn_index,
        message_id=row.message_id,
        participant_id=row.participant_id,
        participant_name=row.participant_name,
        participant_kind=row.participant_kind,
        status=row.status,
        message_type=row.message_type,
        content_text=row.content_text,
        metadata=row.metadata,
        created_at=row.created_at,
    )


def _to_pending_turn_response(record: PendingTurnRecord) -> PendingTurnResponse:
    return PendingTurnResponse(
        turn_index=record.turn_index,
        message_id=record.message_id,
        participant_id=record.participant_id,
        participant_name=record.participant_name,
        participant_kind=record.participant_kind,
        content_text=record.content_text,
        created_at=record.created_at,
        metadata=record.metadata,
    )


def _to_rejected_turn_response(record: RejectedTurnRecord) -> RejectedTurnResponse:
    return RejectedTurnResponse(
        turn_index=record.turn_index,
        message_id=record.message_id,
        participant_id=record.participant_id,
        participant_name=record.participant_name,
        participant_kind=record.participant_kind,
        content_text=record.content_text,
        failure_type=record.failure_type,
        reasons=record.reasons,
        created_at=record.created_at,
        metadata=record.metadata,
    )


def _to_participant_roster_response(record: ParticipantRosterRecord) -> ParticipantRosterResponse:
    return ParticipantRosterResponse(
        participant_id=record.participant_id,
        kind=record.kind,
        display_name=record.display_name,
        role_label=record.role_label,
        joined_at=record.joined_at,
        left_at=record.left_at,
        muted=record.muted,
        metadata=record.metadata,
    )


@app.post("/internal/events/append", response_model=AppendEventResponse, status_code=201)
def append_event(
    request: AppendEventRequest,
    http_request: Request,
) -> AppendEventResponse:
    _authorize_conversation(
        http_request,
        conversation_id=request.conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
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
def rebuild_conversation_snapshot(
    conversation_id: UUID, http_request: Request
) -> ConversationSnapshotResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
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
    request: StartAutomationConversationRequest,
    response: Response,
    http_request: Request,
) -> StartConversationResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "system"},
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
    )
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
    http_request: Request,
) -> StartConversationResponse:
    _authorize(
        http_request,
        allowed_roles={"admin", "operator", "system"},
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
    )
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
    conversation_id: UUID, request: RunLoopRequest, http_request: Request
) -> RunLoopResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    runner = _build_loop_runner(connection)
    try:
        result: RunLoopResult = runner.run_loop(
            RunLoopInput(
                conversation_id=conversation_id,
                max_turns=request.max_turns,
                require_citations=request.require_citations,
                required_citation_ids=request.required_citation_ids,
                source_document_id=request.source_document_id,
                topic_id=request.topic_id,
                context_turn_window=request.context_turn_window,
                context_evidence_limit=request.context_evidence_limit,
                use_agent_runtime=request.use_agent_runtime,
                agent_max_output_tokens=request.agent_max_output_tokens,
                require_human_approval=request.require_human_approval,
                max_consecutive_rejections=request.max_consecutive_rejections,
                arbitration_enabled=request.arbitration_enabled,
                pause_on_disagreement=request.pause_on_disagreement,
                derailment_guard_enabled=request.derailment_guard_enabled,
                min_topic_keyword_matches=request.min_topic_keyword_matches,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TopicNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoParticipantsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConversationNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ContextBuilderNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except AgentRuntimeNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except AgentRuntimeCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        connection.close()

    return RunLoopResponse(
        conversation_id=result.conversation_id,
        turns_attempted=result.turns_attempted,
        turns_created=result.turns_created,
        turns_pending_approval=result.turns_pending_approval,
        turns_rejected=result.turns_rejected,
        event_seq_last=result.event_seq_last,
        turn_index_last=result.turn_index_last,
        stop_reason=result.stop_reason,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


@app.post(
    "/internal/conversations/{conversation_id}/interventions/apply",
    response_model=ApplyInterventionResponse,
)
def apply_human_intervention(
    conversation_id: UUID, request: ApplyInterventionRequest, http_request: Request
) -> ApplyInterventionResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    service = _build_intervention_service(connection)
    try:
        result: ApplyInterventionResult = service.apply_intervention(
            ApplyInterventionInput(
                conversation_id=conversation_id,
                intervention_type=request.intervention_type,
                actor_participant_id=request.actor_participant_id,
                instruction=request.instruction,
                metadata=request.metadata,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInterventionTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidInterventionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    return ApplyInterventionResponse(
        conversation_id=result.conversation_id,
        status=result.status,
        event_seq_last=result.event_seq_last,
        applied_events=result.applied_events,
        occurred_at=result.occurred_at,
    )


@app.post(
    "/internal/conversations/{conversation_id}/turns/{turn_index}/approval",
    response_model=TurnApprovalResponse,
)
def apply_turn_approval(
    conversation_id: UUID,
    turn_index: int,
    request: TurnApprovalRequest,
    http_request: Request,
) -> TurnApprovalResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    service = _build_turn_approval_service(connection)
    try:
        result: ApplyTurnApprovalResult = service.apply_decision(
            ApplyTurnApprovalInput(
                conversation_id=conversation_id,
                turn_index=turn_index,
                decision=request.decision,
                actor_participant_id=request.actor_participant_id,
                reason=request.reason,
                metadata=request.metadata,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TurnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidApprovalDecisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TurnApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    return TurnApprovalResponse(
        conversation_id=result.conversation_id,
        turn_index=result.turn_index,
        message_status=result.message_status,
        event_seq_last=result.event_seq_last,
        applied_events=result.applied_events,
        occurred_at=result.occurred_at,
    )


@app.post(
    "/internal/conversations/{conversation_id}/turns/approval/batch",
    response_model=BatchTurnApprovalResponse,
)
def apply_batch_turn_approval(
    conversation_id: UUID,
    request: BatchTurnApprovalRequest,
    http_request: Request,
) -> BatchTurnApprovalResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    service = _build_batch_turn_approval_service(connection)
    try:
        result: BatchTurnApprovalResult = service.apply_batch(
            BatchTurnApprovalInput(
                conversation_id=conversation_id,
                actor_participant_id=request.actor_participant_id,
                stop_on_error=request.stop_on_error,
                decisions=[
                    BatchTurnApprovalItemInput(
                        turn_index=item.turn_index,
                        decision=item.decision,
                        reason=item.reason,
                        metadata=item.metadata,
                    )
                    for item in request.decisions
                ],
            )
        )
    finally:
        connection.close()

    return BatchTurnApprovalResponse(
        conversation_id=result.conversation_id,
        processed=result.processed,
        approved=result.approved,
        rejected=result.rejected,
        failed=result.failed,
        results=[
            BatchTurnApprovalItemResponse(
                turn_index=item.turn_index,
                success=item.success,
                message_status=item.message_status,
                event_seq_last=item.event_seq_last,
                applied_events=item.applied_events,
                error_code=item.error_code,
                error_message=item.error_message,
            )
            for item in result.results
        ],
    )


@app.get(
    "/internal/conversations/{conversation_id}/turns/pending-approval",
    response_model=list[PendingTurnResponse],
)
def list_pending_approval_turns(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PendingTurnResponse]:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_pending_turn_service(connection)
    try:
        records: list[PendingTurnRecord] = service.list_pending_turns(
            conversation_id=conversation_id,
            limit=limit,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_pending_turn_response(record) for record in records]


@app.get(
    "/internal/conversations/{conversation_id}/turns/pending-approval/page",
    response_model=PendingTurnPageResponse,
)
def list_pending_approval_turns_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> PendingTurnPageResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    after_turn_index = _parse_cursor(cursor=cursor, prefix="turn")
    connection = _connect()
    service = _build_pending_turn_service(connection)
    try:
        records: list[PendingTurnRecord] = service.list_pending_turns(
            conversation_id=conversation_id,
            limit=limit + 1,
            after_turn_index=after_turn_index,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_cursor(prefix="turn", value=page_records[-1].turn_index)
    return PendingTurnPageResponse(
        items=[_to_pending_turn_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get(
    "/internal/conversations/{conversation_id}/events",
    response_model=list[ConversationEventResponse],
)
def list_conversation_events(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    after_seq_no: int = Query(default=0, ge=0),
) -> list[ConversationEventResponse]:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_event_history_service(connection)
    try:
        records: list[ConversationEventRecord] = service.list_events(
            conversation_id=conversation_id,
            limit=limit,
            after_seq_no=after_seq_no,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_event_response(record) for record in records]


@app.get(
    "/internal/conversations/{conversation_id}/events/page",
    response_model=ConversationEventPageResponse,
)
def list_conversation_events_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ConversationEventPageResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    after_seq_no = _parse_cursor(cursor=cursor, prefix="seq")
    connection = _connect()
    service = _build_event_history_service(connection)
    try:
        records: list[ConversationEventRecord] = service.list_events(
            conversation_id=conversation_id,
            limit=limit + 1,
            after_seq_no=after_seq_no,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_cursor(prefix="seq", value=page_records[-1].seq_no)
    return ConversationEventPageResponse(
        items=[_to_event_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get(
    "/internal/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageResponse],
)
def list_conversation_messages(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    after_turn_index: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> list[ConversationMessageResponse]:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_message_history_service(connection)
    try:
        rows: list[ConversationMessageRecord] = service.list_messages(
            conversation_id=conversation_id,
            limit=limit,
            after_turn_index=after_turn_index,
            status=status,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_message_response(row) for row in rows]


@app.get(
    "/internal/conversations/{conversation_id}/messages/page",
    response_model=ConversationMessagePageResponse,
)
def list_conversation_messages_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> ConversationMessagePageResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    after_turn_index = _parse_cursor(cursor=cursor, prefix="turn")
    connection = _connect()
    service = _build_message_history_service(connection)
    try:
        rows: list[ConversationMessageRecord] = service.list_messages(
            conversation_id=conversation_id,
            limit=limit + 1,
            after_turn_index=after_turn_index,
            status=status,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        next_cursor = _build_cursor(prefix="turn", value=page_rows[-1].turn_index)
    return ConversationMessagePageResponse(
        items=[_to_message_response(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.get("/internal/conversations/{conversation_id}/messages/download")
def download_conversation_messages(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=5000, ge=1, le=20000),
    after_turn_index: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> Response:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_message_export_service(connection)
    try:
        resolved_after_turn_index = _resolve_after_cursor(
            cursor=cursor,
            prefix="turn",
            explicit_after=after_turn_index,
            explicit_name="after_turn_index",
        )
        payload = service.export_jsonl(
            conversation_id=conversation_id,
            limit=limit,
            after_turn_index=resolved_after_turn_index,
            status=status,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    filename = f"conversation-{conversation_id}-messages.jsonl"
    return Response(
        content=payload,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/internal/conversations/{conversation_id}/events/download")
def download_conversation_events(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=5000, ge=1, le=20000),
    after_seq_no: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None),
) -> Response:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_event_export_service(connection)
    try:
        resolved_after_seq_no = _resolve_after_cursor(
            cursor=cursor,
            prefix="seq",
            explicit_after=after_seq_no,
            explicit_name="after_seq_no",
        )
        payload = service.export_jsonl(
            conversation_id=conversation_id,
            limit=limit,
            after_seq_no=resolved_after_seq_no,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    filename = f"conversation-{conversation_id}-events.jsonl"
    return Response(
        content=payload,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get(
    "/internal/conversations/{conversation_id}/turns/rejected",
    response_model=list[RejectedTurnResponse],
)
def list_rejected_turns(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[RejectedTurnResponse]:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_rejected_turn_service(connection)
    try:
        records: list[RejectedTurnRecord] = service.list_rejected_turns(
            conversation_id=conversation_id,
            limit=limit,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_rejected_turn_response(record) for record in records]


@app.get(
    "/internal/conversations/{conversation_id}/turns/rejected/page",
    response_model=RejectedTurnPageResponse,
)
def list_rejected_turns_page(
    conversation_id: UUID,
    http_request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> RejectedTurnPageResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    parsed_before_turn_index = _parse_cursor(cursor=cursor, prefix="turn")
    before_turn_index = parsed_before_turn_index if parsed_before_turn_index > 0 else None
    connection = _connect()
    service = _build_rejected_turn_service(connection)
    try:
        records: list[RejectedTurnRecord] = service.list_rejected_turns(
            conversation_id=conversation_id,
            limit=limit + 1,
            before_turn_index=before_turn_index,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_cursor(prefix="turn", value=page_records[-1].turn_index)
    return RejectedTurnPageResponse(
        items=[_to_rejected_turn_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.post(
    "/internal/conversations/{conversation_id}/participants/{participant_id}/role/switch",
    response_model=SwitchParticipantRoleResponse,
)
def switch_participant_role(
    conversation_id: UUID,
    participant_id: UUID,
    request: SwitchParticipantRoleRequest,
    http_request: Request,
) -> SwitchParticipantRoleResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    service = _build_participant_role_service(connection)
    try:
        result: SwitchParticipantRoleResult = service.switch_role(
            SwitchParticipantRoleInput(
                conversation_id=conversation_id,
                participant_id=participant_id,
                new_role_label=request.new_role_label,
                actor_participant_id=request.actor_participant_id,
                reason=request.reason,
                metadata=request.metadata,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ParticipantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    return SwitchParticipantRoleResponse(
        conversation_id=result.conversation_id,
        participant_id=result.participant_id,
        previous_role_label=result.previous_role_label,
        new_role_label=result.new_role_label,
        event_seq_last=result.event_seq_last,
        occurred_at=result.occurred_at,
    )


@app.get(
    "/internal/conversations/{conversation_id}/participants",
    response_model=list[ParticipantRosterResponse],
)
def list_conversation_participants(
    conversation_id: UUID,
    http_request: Request,
    include_left: bool = Query(default=False),
) -> list[ParticipantRosterResponse]:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_participant_roster_service(connection)
    try:
        records: list[ParticipantRosterRecord] = service.list_participants(
            conversation_id=conversation_id,
            include_left=include_left,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return [_to_participant_roster_response(record) for record in records]


@app.get(
    "/internal/conversations/{conversation_id}/participants/page",
    response_model=ParticipantRosterPageResponse,
)
def list_conversation_participants_page(
    conversation_id: UUID,
    http_request: Request,
    include_left: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ParticipantRosterPageResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    after_joined_at, after_participant_id = _parse_participant_cursor(cursor)
    connection = _connect()
    service = _build_participant_roster_service(connection)
    try:
        records: list[ParticipantRosterRecord] = service.list_participants(
            conversation_id=conversation_id,
            include_left=include_left,
            limit=limit + 1,
            after_joined_at=after_joined_at,
            after_participant_id=after_participant_id,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()

    has_more = len(records) > limit
    page_records = records[:limit]
    next_cursor = None
    if has_more and page_records:
        next_cursor = _build_participant_cursor(page_records[-1])
    return ParticipantRosterPageResponse(
        items=[_to_participant_roster_response(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@app.post(
    "/internal/conversations/{conversation_id}/participants/{participant_id}/moderation",
    response_model=ApplyParticipantModerationResponse,
)
def apply_participant_moderation(
    conversation_id: UUID,
    participant_id: UUID,
    request: ApplyParticipantModerationRequest,
    http_request: Request,
) -> ApplyParticipantModerationResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "system"},
    )
    connection = _connect()
    service = _build_participant_moderation_service(connection)
    try:
        result: ApplyParticipantModerationResult = service.apply(
            ApplyParticipantModerationInput(
                conversation_id=conversation_id,
                participant_id=participant_id,
                action=request.action,
                actor_participant_id=request.actor_participant_id,
                reason=request.reason,
                metadata=request.metadata,
            )
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ParticipantModerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidModerationActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ParticipantModerationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()

    return ApplyParticipantModerationResponse(
        conversation_id=result.conversation_id,
        participant_id=result.participant_id,
        muted=result.muted,
        event_type=result.event_type,
        event_seq_last=result.event_seq_last,
        occurred_at=result.occurred_at,
    )


@app.get(
    "/internal/conversations/{conversation_id}/ops/summary",
    response_model=ConversationOpsSummaryResponse,
)
def get_conversation_ops_summary(
    conversation_id: UUID, http_request: Request
) -> ConversationOpsSummaryResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_ops_summary_service(connection)
    try:
        summary: ConversationOpsSummary = service.get_summary(conversation_id=conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ConversationOpsSummaryResponse(
        conversation_id=summary.conversation_id,
        status=summary.status,
        title=summary.title,
        objective=summary.objective,
        updated_at=summary.updated_at,
        participant_count=summary.participant_count,
        total_messages=summary.total_messages,
        committed_messages=summary.committed_messages,
        proposed_messages=summary.proposed_messages,
        rejected_messages=summary.rejected_messages,
        validated_messages=summary.validated_messages,
        last_event_seq_no=summary.last_event_seq_no,
        last_event_type=summary.last_event_type,
        last_event_at=summary.last_event_at,
    )


@app.get(
    "/internal/conversations/{conversation_id}/ops/failures",
    response_model=ConversationFailureSummaryResponse,
)
def get_conversation_failure_summary(
    conversation_id: UUID,
    http_request: Request,
) -> ConversationFailureSummaryResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
    connection = _connect()
    service = _build_failure_summary_service(connection)
    try:
        summary: ConversationFailureSummary = service.get_summary(
            conversation_id=conversation_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()

    return ConversationFailureSummaryResponse(
        conversation_id=summary.conversation_id,
        rejected_turns=summary.rejected_turns,
        missing_citation_count=summary.missing_citation_count,
        invalid_citation_count=summary.invalid_citation_count,
        loop_risk_repetition_count=summary.loop_risk_repetition_count,
        topic_derailment_count=summary.topic_derailment_count,
        loop_guard_trigger_count=summary.loop_guard_trigger_count,
        arbitration_requested_count=summary.arbitration_requested_count,
    )


@app.post(
    "/internal/conversations/{conversation_id}/context/assemble",
    response_model=AssembleContextResponse,
)
def assemble_context_packet(
    conversation_id: UUID, request: AssembleContextRequest, http_request: Request
) -> AssembleContextResponse:
    _authorize_conversation(
        http_request,
        conversation_id=conversation_id,
        allowed_roles={"admin", "operator", "viewer", "system"},
    )
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
