"""Turn validation guard for grounding and loop risk checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_CITATION_PATTERN = re.compile(r"\[(?:chunk|cite):([0-9a-fA-F-]{36})\]")


@dataclass(frozen=True)
class TurnValidationInput:
    participant_kind: str
    content_text: str
    require_citations: bool
    allowed_citation_ids: set[str]
    recent_turn_texts: list[str]
    require_topic_alignment: bool = False
    topic_keywords: set[str] = field(default_factory=set)
    min_topic_keyword_matches: int = 1


@dataclass(frozen=True)
class TurnValidationResult:
    is_valid: bool
    failure_type: str | None
    reasons: list[str]
    citations: list[str]


class TurnValidator:
    """Validates a proposed turn before commit."""

    def validate(self, payload: TurnValidationInput) -> TurnValidationResult:
        normalized_content = payload.content_text.strip()
        if not normalized_content:
            return TurnValidationResult(
                is_valid=False,
                failure_type="empty_content",
                reasons=["content_text is empty"],
                citations=[],
            )

        normalized_recent = {self._normalize(text) for text in payload.recent_turn_texts}
        if self._normalize(normalized_content) in normalized_recent:
            return TurnValidationResult(
                is_valid=False,
                failure_type="loop_risk_repetition",
                reasons=["content repeats a recent turn"],
                citations=[],
            )

        citations = self._extract_citations(normalized_content)
        if payload.participant_kind == "ai" and payload.require_citations:
            if not citations:
                return TurnValidationResult(
                    is_valid=False,
                    failure_type="missing_citation",
                    reasons=["ai turn must include at least one citation"],
                    citations=[],
                )
            unknown = [
                citation
                for citation in citations
                if payload.allowed_citation_ids and citation not in payload.allowed_citation_ids
            ]
            if unknown:
                return TurnValidationResult(
                    is_valid=False,
                    failure_type="invalid_citation",
                    reasons=[f"citation not in allowed evidence set: {unknown[0]}"],
                    citations=citations,
                )

        if payload.participant_kind == "ai" and payload.require_topic_alignment:
            required_matches = max(payload.min_topic_keyword_matches, 1)
            normalized_content_for_match = self._normalize(normalized_content)
            normalized_keywords = {
                self._normalize(keyword)
                for keyword in payload.topic_keywords
                if self._normalize(keyword)
            }
            if normalized_keywords:
                match_count = sum(
                    1
                    for keyword in normalized_keywords
                    if keyword in normalized_content_for_match
                )
                if match_count < required_matches:
                    return TurnValidationResult(
                        is_valid=False,
                        failure_type="topic_derailment",
                        reasons=[
                            "ai turn does not sufficiently align with objective/topic keywords"
                        ],
                        citations=citations,
                    )

        return TurnValidationResult(
            is_valid=True,
            failure_type=None,
            reasons=[],
            citations=citations,
        )

    def _extract_citations(self, content_text: str) -> list[str]:
        return [match.group(1).lower() for match in _CITATION_PATTERN.finditer(content_text)]

    def _normalize(self, value: str) -> str:
        return " ".join(value.lower().split())
