"""Unit tests for batch turn approval workflow service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.conversation_orchestrator.batch_turn_approval_service import (
    BatchTurnApprovalInput,
    BatchTurnApprovalItemInput,
    BatchTurnApprovalService,
)
from services.conversation_orchestrator.turn_approval_service import (
    ApplyTurnApprovalResult,
    TurnNotFoundError,
)


class FakeTurnApprovalService:
    def __init__(self):
        self.calls: list[Any] = []

    def apply_decision(self, payload: Any) -> ApplyTurnApprovalResult:
        self.calls.append(payload)
        if payload.turn_index == 2:
            raise TurnNotFoundError("turn missing")
        status = "committed" if payload.decision == "approve" else "rejected"
        events = ["turn.approved", "turn.committed"] if status == "committed" else ["turn.rejected"]
        return ApplyTurnApprovalResult(
            conversation_id=payload.conversation_id,
            turn_index=payload.turn_index,
            message_status=status,
            event_seq_last=20 + payload.turn_index,
            applied_events=events,
            occurred_at=datetime(2026, 2, 28, 3, 30, tzinfo=timezone.utc),
        )


def test_batch_turn_approval_mixed_success_and_failure() -> None:
    approval_service = FakeTurnApprovalService()
    service = BatchTurnApprovalService(approval_service)
    conversation_id = uuid4()

    result = service.apply_batch(
        BatchTurnApprovalInput(
            conversation_id=conversation_id,
            actor_participant_id=uuid4(),
            decisions=[
                BatchTurnApprovalItemInput(
                    turn_index=1,
                    decision="approve",
                    reason=None,
                    metadata={},
                ),
                BatchTurnApprovalItemInput(
                    turn_index=2,
                    decision="approve",
                    reason=None,
                    metadata={},
                ),
                BatchTurnApprovalItemInput(
                    turn_index=3,
                    decision="reject",
                    reason="off-topic",
                    metadata={},
                ),
            ],
        )
    )

    assert result.conversation_id == conversation_id
    assert result.processed == 3
    assert result.approved == 1
    assert result.rejected == 1
    assert result.failed == 1
    assert len(result.results) == 3
    assert result.results[0].success is True
    assert result.results[1].success is False
    assert result.results[1].error_code == "turn_not_found"
    assert result.results[2].message_status == "rejected"


def test_batch_turn_approval_stop_on_error() -> None:
    approval_service = FakeTurnApprovalService()
    service = BatchTurnApprovalService(approval_service)

    result = service.apply_batch(
        BatchTurnApprovalInput(
            conversation_id=uuid4(),
            actor_participant_id=uuid4(),
            stop_on_error=True,
            decisions=[
                BatchTurnApprovalItemInput(
                    turn_index=1,
                    decision="approve",
                    reason=None,
                    metadata={},
                ),
                BatchTurnApprovalItemInput(
                    turn_index=2,
                    decision="approve",
                    reason=None,
                    metadata={},
                ),
                BatchTurnApprovalItemInput(
                    turn_index=3,
                    decision="approve",
                    reason=None,
                    metadata={},
                ),
            ],
        )
    )

    assert result.processed == 2
    assert result.approved == 1
    assert result.failed == 1
    assert len(result.results) == 2
