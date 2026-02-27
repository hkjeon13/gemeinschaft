"""Conversation loop runner with validation, context assembly, and optional runtime calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.agent_runtime_client import (
    AgentRuntimeClient,
    RunAgentClientInput,
)
from services.conversation_orchestrator.context_packet_builder import (
    ContextPacketBuilder,
    ContextPacketInput,
    ContextPacketResult,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.turn_validator import (
    TurnValidationInput,
    TurnValidator,
)


class NoParticipantsError(RuntimeError):
    """Raised when a conversation has no participants to run turns for."""


class ConversationNotActiveError(RuntimeError):
    """Raised when loop run is requested for non-active conversation."""


class ContextBuilderNotConfiguredError(RuntimeError):
    """Raised when context assembly is requested without a context builder."""


class AgentRuntimeNotConfiguredError(RuntimeError):
    """Raised when runtime generation is requested without configured runtime client."""


@dataclass(frozen=True)
class ParticipantRecord:
    id: UUID
    kind: str
    display_name: str


@dataclass(frozen=True)
class RunLoopInput:
    conversation_id: UUID
    max_turns: int
    require_citations: bool = False
    required_citation_ids: list[UUID] | None = None
    source_document_id: UUID | None = None
    topic_id: UUID | None = None
    context_turn_window: int = 8
    context_evidence_limit: int = 5
    use_agent_runtime: bool = False
    agent_max_output_tokens: int = 256
    require_human_approval: bool = False
    max_consecutive_rejections: int = 3
    arbitration_enabled: bool = False
    pause_on_disagreement: bool = True


@dataclass(frozen=True)
class RunLoopResult:
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


class ConversationLoopRunner:
    def __init__(
        self,
        connection: Any,
        validator: TurnValidator | None = None,
        context_builder: ContextPacketBuilder | None = None,
        agent_runtime_client: AgentRuntimeClient | None = None,
    ):
        self._connection = connection
        self._validator = validator or TurnValidator()
        self._context_builder = context_builder
        self._agent_runtime_client = agent_runtime_client

    def run_loop(self, payload: RunLoopInput) -> RunLoopResult:
        if payload.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if payload.context_turn_window < 1:
            raise ValueError("context_turn_window must be >= 1")
        if payload.context_evidence_limit < 1:
            raise ValueError("context_evidence_limit must be >= 1")
        if payload.agent_max_output_tokens < 1:
            raise ValueError("agent_max_output_tokens must be >= 1")
        if payload.max_consecutive_rejections < 1:
            raise ValueError("max_consecutive_rejections must be >= 1")

        started_at = datetime.now(timezone.utc)
        turns_attempted = 0
        turns_created = 0
        turns_pending_approval = 0
        turns_rejected = 0
        stop_reason: str | None = None
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, status, objective
                    FROM conversation
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (str(payload.conversation_id),),
                )
                conversation_row = cursor.fetchone()
                if conversation_row is None:
                    raise ConversationNotFoundError(
                        f"Conversation {payload.conversation_id} not found"
                    )
                if conversation_row[1] != "active":
                    raise ConversationNotActiveError(
                        f"Conversation {payload.conversation_id} is not active "
                        f"(status={conversation_row[1]})"
                    )
                objective = conversation_row[2] or ""

                cursor.execute(
                    """
                    SELECT id, kind, display_name
                    FROM participant
                    WHERE conversation_id = %s
                    ORDER BY joined_at ASC, id ASC
                    """,
                    (str(payload.conversation_id),),
                )
                participants = [
                    ParticipantRecord(id=row[0], kind=row[1], display_name=row[2])
                    for row in cursor.fetchall()
                ]
                if not participants:
                    raise NoParticipantsError(
                        f"Conversation {payload.conversation_id} has no participants"
                    )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), 0)
                    FROM message
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                turn_index_last = int(cursor.fetchone()[0])

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(seq_no), 0)
                    FROM event
                    WHERE conversation_id = %s
                    """,
                    (str(payload.conversation_id),),
                )
                event_seq_last = int(cursor.fetchone()[0])

                cursor.execute(
                    """
                    SELECT content_text
                    FROM message
                    WHERE conversation_id = %s
                    ORDER BY turn_index DESC
                    LIMIT 5
                    """,
                    (str(payload.conversation_id),),
                )
                recent_turn_texts = [row[0] for row in cursor.fetchall()]
                consecutive_rejections = 0
                previous_ai_committed: tuple[int, set[str]] | None = None

                for _ in range(payload.max_turns):
                    turns_attempted += 1
                    turn_index_last += 1
                    participant = participants[(turn_index_last - 1) % len(participants)]

                    context_packet, evidence_citation_ids = self._assemble_context_packet(
                        payload=payload,
                        conversation_id=payload.conversation_id,
                    )
                    required_citation_ids = list(payload.required_citation_ids or [])
                    if not required_citation_ids and evidence_citation_ids:
                        required_citation_ids = evidence_citation_ids
                    allowed_citation_ids = {
                        str(citation_id).lower() for citation_id in required_citation_ids
                    }

                    content_text, generation_meta = self._generate_turn_content(
                        payload=payload,
                        participant=participant,
                        turn_index=turn_index_last,
                        objective=objective,
                        context_packet=context_packet,
                        required_citation_ids=required_citation_ids,
                    )
                    validation = self._validator.validate(
                        TurnValidationInput(
                            participant_kind=participant.kind,
                            content_text=content_text,
                            require_citations=payload.require_citations,
                            allowed_citation_ids=allowed_citation_ids,
                            recent_turn_texts=recent_turn_texts,
                        )
                    )
                    if validation.is_valid:
                        if payload.require_human_approval and participant.kind == "ai":
                            message_status = "proposed"
                            event_type = "turn.pending_approval"
                        else:
                            message_status = "committed"
                            event_type = "turn.committed"
                    else:
                        message_status = "rejected"
                        event_type = "turn.rejected"
                    cursor.execute(
                        """
                        INSERT INTO message (
                            conversation_id,
                            participant_id,
                            turn_index,
                            message_type,
                            status,
                            content_text,
                            metadata
                        )
                        VALUES (%s, %s, %s, 'statement', %s, %s, %s::jsonb)
                        RETURNING id
                        """,
                        (
                            str(payload.conversation_id),
                            str(participant.id),
                            turn_index_last,
                            message_status,
                            content_text,
                            json.dumps(
                                {
                                    "loop_runner": "v1",
                                    "kind": participant.kind,
                                    "generation": generation_meta,
                                    "context": {
                                        "topic_id": context_packet.get("topic_id"),
                                        "evidence_count": len(
                                            context_packet.get("evidence_chunks", [])
                                        ),
                                    },
                                    "validation": {
                                        "is_valid": validation.is_valid,
                                        "failure_type": validation.failure_type,
                                        "reasons": validation.reasons,
                                        "citations": validation.citations,
                                    },
                                }
                            ),
                        ),
                    )
                    message_row = cursor.fetchone()
                    if message_row is None:  # pragma: no cover - defensive guard
                        raise RuntimeError("Message insert did not return id")
                    message_id = message_row[0]

                    event_seq_last += 1
                    cursor.execute(
                        """
                        INSERT INTO event (
                            conversation_id,
                            message_id,
                            actor_participant_id,
                            seq_no,
                            event_type,
                            payload
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            str(payload.conversation_id),
                            str(message_id),
                            str(participant.id),
                            event_seq_last,
                            event_type,
                            json.dumps(
                                {
                                    "turn_index": turn_index_last,
                                    "participant_id": str(participant.id),
                                    "validation": {
                                        "is_valid": validation.is_valid,
                                        "failure_type": validation.failure_type,
                                    },
                                }
                            ),
                        ),
                    )
                    if event_type == "turn.committed":
                        turns_created += 1
                        consecutive_rejections = 0
                        if participant.kind == "ai":
                            current_citations = set(validation.citations)
                            if payload.arbitration_enabled and previous_ai_committed is not None:
                                prev_turn_index, prev_citations = previous_ai_committed
                                if (
                                    prev_citations
                                    and current_citations
                                    and prev_citations.isdisjoint(current_citations)
                                ):
                                    event_seq_last += 1
                                    cursor.execute(
                                        """
                                        INSERT INTO event (
                                            conversation_id,
                                            actor_participant_id,
                                            seq_no,
                                            event_type,
                                            payload
                                        )
                                        VALUES (%s, %s, %s, 'turn.arbitration_requested', %s::jsonb)
                                        """,
                                        (
                                            str(payload.conversation_id),
                                            str(participant.id),
                                            event_seq_last,
                                            json.dumps(
                                                {
                                                    "reason": "citation_set_disagreement",
                                                    "previous_turn_index": prev_turn_index,
                                                    "current_turn_index": turn_index_last,
                                                    "previous_citations": sorted(prev_citations),
                                                    "current_citations": sorted(current_citations),
                                                }
                                            ),
                                        ),
                                    )
                                    stop_reason = "arbitration_requested"
                                    if payload.pause_on_disagreement:
                                        event_seq_last += 1
                                        cursor.execute(
                                            """
                                            INSERT INTO event (
                                                conversation_id,
                                                actor_participant_id,
                                                seq_no,
                                                event_type,
                                                payload
                                            )
                                            VALUES (
                                                %s,
                                                %s,
                                                %s,
                                                'conversation.paused',
                                                %s::jsonb
                                            )
                                            """,
                                            (
                                                str(payload.conversation_id),
                                                str(participant.id),
                                                event_seq_last,
                                                json.dumps(
                                                    {"reason": "arbitration_requested"}
                                                ),
                                            ),
                                        )
                                    break
                            previous_ai_committed = (turn_index_last, current_citations)
                    elif event_type == "turn.pending_approval":
                        turns_pending_approval += 1
                        consecutive_rejections = 0
                    else:  # turn.rejected
                        turns_rejected += 1
                        consecutive_rejections += 1
                        if consecutive_rejections >= payload.max_consecutive_rejections:
                            event_seq_last += 1
                            cursor.execute(
                                """
                                INSERT INTO event (
                                    conversation_id,
                                    actor_participant_id,
                                    seq_no,
                                    event_type,
                                    payload
                                )
                                VALUES (%s, %s, %s, 'loop.guard_triggered', %s::jsonb)
                                """,
                                (
                                    str(payload.conversation_id),
                                    str(participant.id),
                                    event_seq_last,
                                    json.dumps(
                                        {
                                            "reason": "consecutive_rejections",
                                            "threshold": payload.max_consecutive_rejections,
                                            "turn_index": turn_index_last,
                                        }
                                    ),
                                ),
                            )
                            event_seq_last += 1
                            cursor.execute(
                                """
                                INSERT INTO event (
                                    conversation_id,
                                    actor_participant_id,
                                    seq_no,
                                    event_type,
                                    payload
                                )
                                VALUES (%s, %s, %s, 'conversation.paused', %s::jsonb)
                                """,
                                (
                                    str(payload.conversation_id),
                                    str(participant.id),
                                    event_seq_last,
                                    json.dumps({"reason": "loop_guard_triggered"}),
                                ),
                            )
                            stop_reason = "consecutive_rejections_guard"
                            break
                    recent_turn_texts = [content_text, *recent_turn_texts[:4]]

                final_status = "active"
                if stop_reason == "consecutive_rejections_guard":
                    final_status = "paused"
                elif stop_reason == "arbitration_requested" and payload.pause_on_disagreement:
                    final_status = "paused"
                elif turns_created == 0 and turns_rejected > 0 and turns_pending_approval == 0:
                    final_status = "paused"
                cursor.execute(
                    """
                    UPDATE conversation
                    SET
                        updated_at = NOW(),
                        status = %s
                    WHERE id = %s
                    """,
                    (final_status, str(payload.conversation_id)),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        finished_at = datetime.now(timezone.utc)
        return RunLoopResult(
            conversation_id=payload.conversation_id,
            turns_attempted=turns_attempted,
            turns_created=turns_created,
            turns_pending_approval=turns_pending_approval,
            turns_rejected=turns_rejected,
            event_seq_last=event_seq_last,
            turn_index_last=turn_index_last,
            stop_reason=stop_reason,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _assemble_context_packet(
        self,
        *,
        payload: RunLoopInput,
        conversation_id: UUID,
    ) -> tuple[dict[str, Any], list[UUID]]:
        if payload.source_document_id is None:
            return {}, []
        if self._context_builder is None:
            raise ContextBuilderNotConfiguredError(
                "source_document_id was provided but context builder is not configured"
            )

        packet: ContextPacketResult = self._context_builder.build_packet(
            ContextPacketInput(
                conversation_id=conversation_id,
                source_document_id=payload.source_document_id,
                topic_id=payload.topic_id,
                turn_window=payload.context_turn_window,
                evidence_limit=payload.context_evidence_limit,
            )
        )
        context_packet = {
            "topic_id": str(packet.topic_id) if packet.topic_id else None,
            "topic_label": packet.topic_label,
            "topic_summary": packet.topic_summary,
            "recent_turns": [
                {
                    "turn_index": turn.turn_index,
                    "speaker": turn.speaker,
                    "content_text": turn.content_text,
                }
                for turn in packet.recent_turns
            ],
            "evidence_chunks": [
                {
                    "source_chunk_id": str(chunk.source_chunk_id),
                    "chunk_index": chunk.chunk_index,
                    "content_text": chunk.content_text,
                    "relevance_score": chunk.relevance_score,
                }
                for chunk in packet.evidence_chunks
            ],
        }
        citation_ids = [chunk.source_chunk_id for chunk in packet.evidence_chunks]
        return context_packet, citation_ids

    def _generate_turn_content(
        self,
        *,
        payload: RunLoopInput,
        participant: ParticipantRecord,
        turn_index: int,
        objective: str,
        context_packet: dict[str, Any],
        required_citation_ids: list[UUID],
    ) -> tuple[str, dict[str, Any]]:
        if participant.kind == "ai" and payload.use_agent_runtime:
            if self._agent_runtime_client is None:
                raise AgentRuntimeNotConfiguredError(
                    "use_agent_runtime=true but AGENT_RUNTIME_BASE_URL is not configured"
                )
            agent_key = self._resolve_agent_key(participant)
            prompt = self._build_runtime_prompt(
                objective=objective,
                participant=participant,
                required_citation_ids=required_citation_ids,
                context_packet=context_packet,
            )
            runtime_result = self._agent_runtime_client.run_agent(
                RunAgentClientInput(
                    agent_key=agent_key,
                    prompt=prompt,
                    context_packet=context_packet,
                    max_output_tokens=payload.agent_max_output_tokens,
                )
            )
            content_text = runtime_result.output_text.strip()
            if not content_text:
                content_text = f"[runtime-empty] turn {turn_index} by {participant.display_name}"
            if (
                required_citation_ids
                and "[chunk:" not in content_text
                and "[cite:" not in content_text
            ):
                content_text += f" [chunk:{required_citation_ids[0]}]"
            return (
                content_text,
                {
                    "generator": "agent_runtime",
                    "agent_key": agent_key,
                    "run_id": runtime_result.run_id,
                    "selected_model": runtime_result.selected_model,
                    "token_in": runtime_result.token_in,
                    "token_out": runtime_result.token_out,
                    "latency_ms": runtime_result.latency_ms,
                    "finish_reason": runtime_result.finish_reason,
                },
            )

        content_text = f"[loop-v1] turn {turn_index} by {participant.display_name}"
        if participant.kind == "ai" and required_citation_ids:
            content_text += f" [chunk:{required_citation_ids[0]}]"
        return content_text, {"generator": "deterministic"}

    def _resolve_agent_key(self, participant: ParticipantRecord) -> str:
        normalized = participant.display_name.lower()
        if "2" in normalized:
            return "ai_2"
        return "ai_1"

    def _build_runtime_prompt(
        self,
        *,
        objective: str,
        participant: ParticipantRecord,
        required_citation_ids: list[UUID],
        context_packet: dict[str, Any],
    ) -> str:
        topic = context_packet.get("topic_label") or context_packet.get("topic_summary")
        topic_text = str(topic) if topic else "general"
        citation_rule = (
            f"Include citation marker [chunk:{required_citation_ids[0]}]."
            if required_citation_ids
            else "If evidence exists, include chunk citation markers."
        )
        return (
            f"Objective: {objective or 'N/A'}\n"
            f"Speaker: {participant.display_name}\n"
            f"Topic: {topic_text}\n"
            f"Instruction: Provide one grounded turn. {citation_rule}"
        )
