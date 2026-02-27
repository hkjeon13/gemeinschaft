"""API tests for context packet assemble endpoint."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.context_packet_builder import (
    ContextPacketResult,
    ContextEvidence,
    ContextTurn,
    TopicNotFoundError,
)
from services.conversation_orchestrator.event_store import ConversationNotFoundError


class DummyConnection:
    def close(self) -> None:
        return None


class SuccessBuilder:
    def build_packet(self, payload: Any) -> ContextPacketResult:
        return ContextPacketResult(
            conversation_id=payload.conversation_id,
            source_document_id=payload.source_document_id,
            topic_id=uuid4(),
            topic_label="Refund",
            topic_summary="Refund summary",
            recent_turns=[ContextTurn(turn_index=1, speaker="AI(1)", content_text="hello")],
            evidence_chunks=[
                ContextEvidence(
                    source_chunk_id=uuid4(),
                    chunk_index=0,
                    content_text="evidence",
                    relevance_score=0.97,
                )
            ],
        )


class ConversationNotFoundBuilder:
    def build_packet(self, payload: Any) -> ContextPacketResult:
        raise ConversationNotFoundError(f"Conversation {payload.conversation_id} not found")


class TopicNotFoundBuilder:
    def build_packet(self, payload: Any) -> ContextPacketResult:
        raise TopicNotFoundError("Topic missing")


def test_assemble_context_endpoint_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_context_packet_builder",
        lambda connection: SuccessBuilder(),
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = str(uuid4())

    response = client.post(
        f"/internal/conversations/{conversation_id}/context/assemble",
        json={
            "source_document_id": str(uuid4()),
            "turn_window": 6,
            "evidence_limit": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["topic_label"] == "Refund"
    assert len(payload["recent_turns"]) == 1
    assert len(payload["evidence_chunks"]) == 1


def test_assemble_context_endpoint_conversation_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_context_packet_builder",
        lambda connection: ConversationNotFoundBuilder(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/context/assemble",
        json={"source_document_id": str(uuid4())},
    )

    assert response.status_code == 404


def test_assemble_context_endpoint_topic_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_context_packet_builder",
        lambda connection: TopicNotFoundBuilder(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.post(
        f"/internal/conversations/{uuid4()}/context/assemble",
        json={"source_document_id": str(uuid4()), "topic_id": str(uuid4())},
    )

    assert response.status_code == 404
