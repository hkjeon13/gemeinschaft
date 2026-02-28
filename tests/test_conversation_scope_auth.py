"""Conversation scope auth guard tests."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from services.conversation_orchestrator import app as orchestrator_app_module
from services.conversation_orchestrator.pending_turn_service import PendingTurnRecord
from services.conversation_orchestrator.participant_roster_service import (
    ParticipantRosterRecord,
)
from services.conversation_orchestrator.rejected_turn_service import RejectedTurnRecord
from services.conversation_orchestrator.message_history_service import (
    ConversationMessageRecord,
)


class ScopeConnection:
    def __init__(self, tenant_id: Any, workspace_id: Any):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.closed = False
        self._last_fetchone: Any = None

    def cursor(self) -> "ScopeConnection":
        return self

    def __enter__(self) -> "ScopeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "select tenant_id, workspace_id from conversation where id = %s" in normalized_sql:
            self._last_fetchone = (self.tenant_id, self.workspace_id)
            return
        raise AssertionError(f"Unexpected SQL in scope fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def close(self) -> None:
        self.closed = True


class SuccessMessageHistoryService:
    def list_messages(
        self,
        *,
        conversation_id: Any,
        limit: int = 50,
        after_turn_index: int = 0,
        status: str | None = None,
    ) -> list[ConversationMessageRecord]:
        del conversation_id, limit, after_turn_index, status
        return []


class CountingPendingTurnService:
    calls = 0

    def list_pending_turns(
        self,
        *,
        conversation_id: Any,
        limit: int = 20,
        after_turn_index: int = 0,
    ) -> list[PendingTurnRecord]:
        del conversation_id, limit, after_turn_index
        type(self).calls += 1
        return []


class CountingRejectedTurnService:
    calls = 0

    def list_rejected_turns(
        self,
        *,
        conversation_id: Any,
        limit: int = 20,
        before_turn_index: int | None = None,
    ) -> list[RejectedTurnRecord]:
        del conversation_id, limit, before_turn_index
        type(self).calls += 1
        return []


class CountingParticipantRosterService:
    calls = 0

    def list_participants(
        self,
        *,
        conversation_id: Any,
        include_left: bool = False,
        limit: int = 100,
        after_joined_at: Any | None = None,
        after_participant_id: Any | None = None,
    ) -> list[ParticipantRosterRecord]:
        del conversation_id, include_left, limit, after_joined_at, after_participant_id
        type(self).calls += 1
        return []


def test_conversation_messages_scope_mismatch_returns_403(monkeypatch: Any) -> None:
    resource_tenant = uuid4()
    resource_workspace = uuid4()
    auth_tenant = uuid4()
    auth_workspace = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(resource_tenant, resource_workspace),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: SuccessMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages",
        headers={
            "x-auth-tenant-id": str(auth_tenant),
            "x-auth-workspace-id": str(auth_workspace),
        },
    )

    assert response.status_code == 403


def test_conversation_messages_scope_match_returns_200(monkeypatch: Any) -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(tenant_id, workspace_id),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_message_history_service",
        lambda connection: SuccessMessageHistoryService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/messages",
        headers={
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200


def test_pending_turns_page_scope_mismatch_returns_403_without_service_call(
    monkeypatch: Any,
) -> None:
    CountingPendingTurnService.calls = 0
    resource_tenant = uuid4()
    resource_workspace = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(resource_tenant, resource_workspace),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: CountingPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval/page",
        headers={
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert CountingPendingTurnService.calls == 0


def test_pending_turns_page_scope_match_returns_200(monkeypatch: Any) -> None:
    CountingPendingTurnService.calls = 0
    tenant_id = uuid4()
    workspace_id = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(tenant_id, workspace_id),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_pending_turn_service",
        lambda connection: CountingPendingTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/pending-approval/page",
        headers={
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200
    assert CountingPendingTurnService.calls == 1


def test_rejected_turns_page_scope_mismatch_returns_403_without_service_call(
    monkeypatch: Any,
) -> None:
    CountingRejectedTurnService.calls = 0
    resource_tenant = uuid4()
    resource_workspace = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(resource_tenant, resource_workspace),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: CountingRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected/page",
        headers={
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert CountingRejectedTurnService.calls == 0


def test_rejected_turns_page_scope_match_returns_200(monkeypatch: Any) -> None:
    CountingRejectedTurnService.calls = 0
    tenant_id = uuid4()
    workspace_id = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(tenant_id, workspace_id),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_rejected_turn_service",
        lambda connection: CountingRejectedTurnService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/turns/rejected/page",
        headers={
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200
    assert CountingRejectedTurnService.calls == 1


def test_participants_page_scope_mismatch_returns_403_without_service_call(
    monkeypatch: Any,
) -> None:
    CountingParticipantRosterService.calls = 0
    resource_tenant = uuid4()
    resource_workspace = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(resource_tenant, resource_workspace),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: CountingParticipantRosterService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/participants/page",
        headers={
            "x-auth-tenant-id": str(uuid4()),
            "x-auth-workspace-id": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert CountingParticipantRosterService.calls == 0


def test_participants_page_scope_match_returns_200(monkeypatch: Any) -> None:
    CountingParticipantRosterService.calls = 0
    tenant_id = uuid4()
    workspace_id = uuid4()
    monkeypatch.setattr(
        orchestrator_app_module,
        "_connect",
        lambda: ScopeConnection(tenant_id, workspace_id),
    )
    monkeypatch.setattr(
        orchestrator_app_module,
        "_build_participant_roster_service",
        lambda connection: CountingParticipantRosterService(),
    )
    client = TestClient(orchestrator_app_module.app)

    response = client.get(
        f"/internal/conversations/{uuid4()}/participants/page",
        headers={
            "x-auth-tenant-id": str(tenant_id),
            "x-auth-workspace-id": str(workspace_id),
        },
    )

    assert response.status_code == 200
    assert CountingParticipantRosterService.calls == 1
