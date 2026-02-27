"""Unit tests for turn validation guard."""

from __future__ import annotations

from uuid import uuid4

from services.conversation_orchestrator.turn_validator import (
    TurnValidationInput,
    TurnValidator,
)


def test_validator_accepts_ai_turn_with_valid_citation() -> None:
    citation_id = str(uuid4())
    validator = TurnValidator()

    result = validator.validate(
        TurnValidationInput(
            participant_kind="ai",
            content_text=f"Grounded answer [chunk:{citation_id}]",
            require_citations=True,
            allowed_citation_ids={citation_id},
            recent_turn_texts=[],
        )
    )

    assert result.is_valid is True
    assert result.failure_type is None
    assert result.citations == [citation_id]


def test_validator_rejects_missing_citation_for_ai() -> None:
    validator = TurnValidator()

    result = validator.validate(
        TurnValidationInput(
            participant_kind="ai",
            content_text="Ungrounded answer",
            require_citations=True,
            allowed_citation_ids=set(),
            recent_turn_texts=[],
        )
    )

    assert result.is_valid is False
    assert result.failure_type == "missing_citation"


def test_validator_rejects_invalid_citation() -> None:
    validator = TurnValidator()
    allowed = str(uuid4())
    invalid = str(uuid4())

    result = validator.validate(
        TurnValidationInput(
            participant_kind="ai",
            content_text=f"Answer [chunk:{invalid}]",
            require_citations=True,
            allowed_citation_ids={allowed},
            recent_turn_texts=[],
        )
    )

    assert result.is_valid is False
    assert result.failure_type == "invalid_citation"


def test_validator_rejects_repeated_content() -> None:
    validator = TurnValidator()

    result = validator.validate(
        TurnValidationInput(
            participant_kind="ai",
            content_text="Repeat this thought",
            require_citations=False,
            allowed_citation_ids=set(),
            recent_turn_texts=["repeat this thought"],
        )
    )

    assert result.is_valid is False
    assert result.failure_type == "loop_risk_repetition"


def test_validator_accepts_human_without_citation_requirement() -> None:
    validator = TurnValidator()

    result = validator.validate(
        TurnValidationInput(
            participant_kind="human",
            content_text="Let's redirect to a different angle.",
            require_citations=True,
            allowed_citation_ids=set(),
            recent_turn_texts=[],
        )
    )

    assert result.is_valid is True
    assert result.failure_type is None
