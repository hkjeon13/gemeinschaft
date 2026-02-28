"""API tests for participant roster endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.event_store import ConversationNotFoundError
from services.conversation_orchestrator.participant_roster_service import (
    ParticipantRosterRecord,
)


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SuccessParticipantRosterService:
    def __init__(self):
        self.last_include_left: bool | None = None

    def list_participants(
        self,
        *,
        conversation_id: Any,
        include_left: bool = False,
        limit: int = 100,
        after_joined_at: datetime | None = None,
        after_participant_id: Any | None = None,
    ) -> list[ParticipantRosterRecord]:
        del conversation_id, limit, after_joined_at, after_participant_id
        self.last_include_left = include_left
        return [
            ParticipantRosterRecord(
                participant_id=uuid4(),
                kind="ai",
                display_name="AI(2)",
                role_label="observer",
                joined_at=datetime(2026, 2, 28, 3, 20, tzinfo=timezone.utc),
                left_at=None,
                muted=False,
                metadata={"moderation": {"muted": False}},
            )
        ]


class MissingConversationParticipantRosterService:
    def list_participants(
        self,
        *,
        conversation_id: Any,
        include_left: bool = False,
        limit: int = 100,
        after_joined_at: datetime | None = None,
        after_participant_id: Any | None = None,
    ) -> list[ParticipantRosterRecord]:
        del include_left, limit, after_joined_at, after_participant_id
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


class CursorParticipantRosterService:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._joined_at_1 = datetime(2026, 2, 28, 3, 20, tzinfo=timezone.utc)
        self._joined_at_2 = datetime(2026, 2, 28, 3, 21, tzinfo=timezone.utc)
        self._participant_id_1 = uuid4()
        self._participant_id_2 = uuid4()

    def list_participants(
        self,
        *,
        conversation_id: Any,
        include_left: bool = False,
        limit: int = 100,
        after_joined_at: datetime | None = None,
        after_participant_id: Any | None = None,
    ) -> list[ParticipantRosterRecord]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "include_left": include_left,
                "limit": limit,
                "after_joined_at": after_joined_at,
                "after_participant_id": after_participant_id,
            }
        )
        rows = [
            ParticipantRosterRecord(
                participant_id=self._participant_id_1,
                kind="ai",
                display_name="AI(1)",
                role_label="analyst",
                joined_at=self._joined_at_1,
                left_at=None,
                muted=False,
                metadata={},
            ),
            ParticipantRosterRecord(
                participant_id=self._participant_id_2,
                kind="human",
                display_name="Reviewer",
                role_label="owner",
                joined_at=self._joined_at_2,
                left_at=None,
                muted=False,
                metadata={},
            ),
        ]
        if after_joined_at is not None and after_participant_id is not None:
            rows = [
                row
                for row in rows
                if row.joined_at > after_joined_at
                or (row.joined_at == after_joined_at and row.participant_id > after_participant_id)
            ]
        return rows[:limit]


def test_list_conversation_participants_success(monkeypatch: Any) -> None:
    service = SuccessParticipantRosterService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/participants",
        params={"include_left": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["display_name"] == "AI(2)"
    assert payload[0]["role_label"] == "observer"
    assert payload[0]["muted"] is False
    assert service.last_include_left is True


def test_list_conversation_participants_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: MissingConversationParticipantRosterService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(f"/internal/conversations/{uuid4()}/participants")

    assert response.status_code == 404


def test_list_conversation_participants_page_success(monkeypatch: Any) -> None:
    service = CursorParticipantRosterService()
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: service,
    )
    client = TestClient(orchestrator_app_module.app)
    conversation_id = uuid4()

    first = client.get(
        f"/internal/conversations/{conversation_id}/participants/page",
        params={"limit": 1},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 1
    assert first_payload["items"][0]["participant_id"] == str(service._participant_id_1)
    assert first_payload["next_cursor"] == f"p:2026-02-28T03:20:00Z|{service._participant_id_1}"
    assert first_payload["has_more"] is True
    assert service.calls[0]["limit"] == 2
    assert service.calls[0]["after_joined_at"] is None

    second = client.get(
        f"/internal/conversations/{conversation_id}/participants/page",
        params={"limit": 1, "cursor": first_payload["next_cursor"]},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["items"][0]["participant_id"] == str(service._participant_id_2)
    assert second_payload["next_cursor"] is None
    assert second_payload["has_more"] is False
    assert service.calls[1]["limit"] == 2
    assert service.calls[1]["after_joined_at"] == service._joined_at_1
    assert service.calls[1]["after_participant_id"] == service._participant_id_1


def test_list_conversation_participants_page_invalid_cursor(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator_app_module, "_connect", lambda: DummyConnection())
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: CursorParticipantRosterService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/participants/page",
        params={"cursor": "bad"},
    )

    assert response.status_code == 400
