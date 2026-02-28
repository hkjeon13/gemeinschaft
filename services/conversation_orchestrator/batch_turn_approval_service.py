"""Batch approval workflow service for proposed turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.turn_approval_service import (
    ApplyTurnApprovalInput,
    InvalidApprovalDecisionError,
    TurnApprovalService,
    TurnApprovalStateError,
    TurnNotFoundError,
)


@dataclass(frozen=True)
class BatchTurnApprovalItemInput:
    turn_index: int
    decision: str
    reason: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class BatchTurnApprovalInput:
    conversation_id: UUID
    actor_participant_id: UUID | None
    decisions: list[BatchTurnApprovalItemInput]
    stop_on_error: bool = False


@dataclass(frozen=True)
class BatchTurnApprovalItemResult:
    turn_index: int
    success: bool
    message_status: str | None
    event_seq_last: int | None
    applied_events: list[str]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class BatchTurnApprovalResult:
    conversation_id: UUID
    processed: int
    approved: int
    rejected: int
    failed: int
    results: list[BatchTurnApprovalItemResult]


class BatchTurnApprovalService:
    def __init__(self, turn_approval_service: TurnApprovalService):
        self._turn_approval_service = turn_approval_service

    def apply_batch(self, payload: BatchTurnApprovalInput) -> BatchTurnApprovalResult:
        processed = 0
        approved = 0
        rejected = 0
        failed = 0
        results: list[BatchTurnApprovalItemResult] = []

        for item in payload.decisions:
            processed += 1
            try:
                result = self._turn_approval_service.apply_decision(
                    ApplyTurnApprovalInput(
                        conversation_id=payload.conversation_id,
                        turn_index=item.turn_index,
                        decision=item.decision,
                        actor_participant_id=payload.actor_participant_id,
                        reason=item.reason,
                        metadata=item.metadata,
                    )
                )
                if result.message_status == "committed":
                    approved += 1
                elif result.message_status == "rejected":
                    rejected += 1
                results.append(
                    BatchTurnApprovalItemResult(
                        turn_index=item.turn_index,
                        success=True,
                        message_status=result.message_status,
                        event_seq_last=result.event_seq_last,
                        applied_events=result.applied_events,
                        error_code=None,
                        error_message=None,
                    )
                )
            except (
                ConversationNotFoundError,
                TurnNotFoundError,
                InvalidApprovalDecisionError,
                TurnApprovalStateError,
            ) as exc:
                failed += 1
                results.append(
                    BatchTurnApprovalItemResult(
                        turn_index=item.turn_index,
                        success=False,
                        message_status=None,
                        event_seq_last=None,
                        applied_events=[],
                        error_code=self._error_code(exc),
                        error_message=str(exc),
                    )
                )
                if payload.stop_on_error:
                    break

        return BatchTurnApprovalResult(
            conversation_id=payload.conversation_id,
            processed=processed,
            approved=approved,
            rejected=rejected,
            failed=failed,
            results=results,
        )

    def _error_code(self, exc: Exception) -> str:
        if isinstance(exc, ConversationNotFoundError):
            return "conversation_not_found"
        if isinstance(exc, TurnNotFoundError):
            return "turn_not_found"
        if isinstance(exc, InvalidApprovalDecisionError):
            return "invalid_decision"
        if isinstance(exc, TurnApprovalStateError):
            return "invalid_turn_state"
        return "unknown_error"
