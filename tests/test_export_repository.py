"""Unit tests for export repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.export_service.repository import (
    ConversationForExportNotFoundError,
    CreateExportJobInput,
    DatasetVersionNotFoundError,
    ExportArtifactNotFoundError,
    ExportJobNotFoundError,
    ExportRepository,
    InvalidExportStorageKeyError,
)


class FakeConnection:
    def __init__(
        self,
        *,
        conversation_row: tuple[Any, ...] | None,
        message_rows: list[tuple[Any, ...]],
        conversation_scope_row: tuple[Any, ...] | None = None,
        export_job_row: tuple[Any, ...] | None = None,
        initial_event_seq: int = 0,
        initial_dataset_version_no: int = 0,
        dataset_version_rows: list[tuple[Any, ...]] | None = None,
    ):
        self.conversation_row = conversation_row
        self.message_rows = message_rows
        self.conversation_scope_row = conversation_scope_row
        self.export_job_row = export_job_row
        self.initial_event_seq = initial_event_seq
        self.initial_dataset_version_no = initial_dataset_version_no
        self.dataset_version_rows = dataset_version_rows or []
        self.commit_calls = 0
        self.rollback_calls = 0
        self._last_fetchone: Any = None
        self._last_fetchall: list[Any] = []
        self.inserted_export_job_id: str | None = None
        self.inserted_storage_key: str | None = None
        self.inserted_manifest: dict[str, Any] | None = None
        self.inserted_event_type: str | None = None
        self.inserted_event_seq: int | None = None
        self.inserted_event_payload: dict[str, Any] | None = None
        self.inserted_dataset_version_no: int | None = None
        self.inserted_dataset_manifest: dict[str, Any] | None = None

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if (
            "from conversation where id = %s and tenant_id = %s and workspace_id = %s"
        ) in normalized_sql:
            self._last_fetchone = self.conversation_row
            return
        if "from message m join participant p" in normalized_sql:
            self._last_fetchall = self.message_rows
            return
        if (
            "select coalesce(max(version_no), 0) from conversation_dataset_version "
            "where conversation_id = %s"
        ) in normalized_sql:
            self._last_fetchone = (self.initial_dataset_version_no,)
            return
        if (
            "from conversation_dataset_version where conversation_id = %s "
            "and version_no = %s"
        ) in normalized_sql:
            assert params is not None
            target_version = int(params[1])
            self._last_fetchone = None
            for row in self.dataset_version_rows:
                if int(row[2]) == target_version:
                    self._last_fetchone = row
                    break
            return
        if (
            "from conversation_dataset_version where conversation_id = %s "
            "order by version_no desc limit 1"
        ) in normalized_sql:
            self._last_fetchone = (
                self.dataset_version_rows[0] if self.dataset_version_rows else None
            )
            return
        if "from conversation_dataset_version where conversation_id = %s" in normalized_sql:
            self._last_fetchall = self.dataset_version_rows
            return
        if "insert into export_job" in normalized_sql:
            assert params is not None
            self.inserted_export_job_id = str(params[0])
            self.inserted_storage_key = str(params[5])
            self.inserted_manifest = json.loads(str(params[7]))
            if self.export_job_row is None:
                self._last_fetchone = None
            else:
                self._last_fetchone = (
                    UUID(self.inserted_export_job_id),
                    self.export_job_row[1],
                    self.export_job_row[2],
                    self.export_job_row[3],
                )
            return
        if "insert into conversation_dataset_version (" in normalized_sql:
            assert params is not None
            self.inserted_dataset_version_no = int(params[1])
            self.inserted_dataset_manifest = json.loads(str(params[6]))
            self.initial_dataset_version_no = int(params[1])
            return
        if (
            "select coalesce(max(seq_no), 0) from event where conversation_id = %s"
        ) in normalized_sql:
            self._last_fetchone = (self.initial_event_seq,)
            return
        if "insert into event (" in normalized_sql:
            assert params is not None
            self.inserted_event_seq = int(params[1])
            self.inserted_event_payload = json.loads(str(params[2]))
            self.inserted_event_type = (
                "export.completed" if "export.completed" in normalized_sql else None
            )
            return
        if "from export_job where id = %s" in normalized_sql:
            self._last_fetchone = self.export_job_row
            return
        if "select tenant_id, workspace_id from conversation where id = %s" in normalized_sql:
            self._last_fetchone = self.conversation_scope_row
            return
        raise AssertionError(f"Unexpected SQL in fake: {normalized_sql}")

    def fetchone(self) -> Any:
        return self._last_fetchone

    def fetchall(self) -> Any:
        return self._last_fetchall

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeExportJobListConnection:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows
        self._last_fetchall: list[Any] = []
        self.last_sql: str | None = None
        self.last_params: tuple[Any, ...] | None = None

    def cursor(self) -> "FakeExportJobListConnection":
        return self

    def __enter__(self) -> "FakeExportJobListConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized_sql = " ".join(sql.lower().split())
        if "from export_job" in normalized_sql and "where conversation_id = %s" in normalized_sql:
            self.last_sql = normalized_sql
            self.last_params = params
            self._last_fetchall = self.rows
            return
        raise AssertionError(f"Unexpected SQL in fake list connection: {normalized_sql}")

    def fetchall(self) -> list[Any]:
        return self._last_fetchall


def _conversation_row(conversation_id: Any) -> tuple[Any, ...]:
    ts = datetime(2026, 2, 27, 21, 30, tzinfo=timezone.utc)
    return (
        conversation_id,
        "Refund triage",
        "Analyze refund anomalies",
        "automation",
        "active",
        ts,
        ts,
        None,
    )


def _message_rows() -> list[tuple[Any, ...]]:
    ts = datetime(2026, 2, 27, 21, 31, tzinfo=timezone.utc)
    return [
        (
            uuid4(),
            1,
            "statement",
            "committed",
            "hello",
            ts,
            uuid4(),
            "ai",
            "AI(1)",
            "analyst",
        ),
        (
            uuid4(),
            2,
            "statement",
            "committed",
            "world",
            ts,
            uuid4(),
            "human",
            "Reviewer",
            "owner",
        ),
    ]


def test_create_export_job_jsonl_writes_file(tmp_path: Path) -> None:
    conversation_id = uuid4()
    tenant_id = uuid4()
    workspace_id = uuid4()
    export_job_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 32, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=_conversation_row(conversation_id),
        message_rows=_message_rows(),
        export_job_row=(export_job_id, "completed", ts, ts),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    result = repository.create_export_job(
        CreateExportJobInput(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            export_format="jsonl",
            requested_by_user_id=None,
        )
    )

    assert connection.inserted_export_job_id is not None
    assert result.job_id == UUID(connection.inserted_export_job_id)
    assert result.row_count == 2
    assert result.status == "completed"
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0
    export_path = Path(result.storage_key)
    assert export_path.exists()
    lines = export_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["turn_index"] == 1
    assert connection.inserted_event_type == "export.completed"
    assert connection.inserted_event_seq == 1
    assert connection.inserted_event_payload is not None
    assert connection.inserted_event_payload["export_job_id"] == str(result.job_id)
    assert connection.inserted_event_payload["dataset_version_no"] == 1
    assert connection.inserted_event_payload["format"] == "jsonl"
    assert connection.inserted_event_payload["row_count"] == 2
    assert connection.inserted_dataset_version_no == 1
    assert connection.inserted_manifest is not None
    assert connection.inserted_manifest["dataset_version_no"] == 1
    assert connection.inserted_dataset_manifest is not None
    assert connection.inserted_dataset_manifest["dataset_version_no"] == 1


def test_create_export_job_csv_writes_header_for_empty_rows(tmp_path: Path) -> None:
    conversation_id = uuid4()
    export_job_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 35, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=_conversation_row(conversation_id),
        message_rows=[],
        export_job_row=(export_job_id, "completed", ts, ts),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    result = repository.create_export_job(
        CreateExportJobInput(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=conversation_id,
            export_format="csv",
            requested_by_user_id=None,
        )
    )

    assert result.row_count == 0
    content = Path(result.storage_key).read_text(encoding="utf-8")
    assert content.startswith("message_id,turn_index")


def test_create_export_job_appends_event_after_existing_sequence(tmp_path: Path) -> None:
    conversation_id = uuid4()
    export_job_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 36, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=_conversation_row(conversation_id),
        message_rows=[],
        export_job_row=(export_job_id, "completed", ts, ts),
        initial_event_seq=9,
        initial_dataset_version_no=4,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    repository.create_export_job(
        CreateExportJobInput(
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=conversation_id,
            export_format="csv",
            requested_by_user_id=None,
        )
    )

    assert connection.inserted_event_type == "export.completed"
    assert connection.inserted_event_seq == 10
    assert connection.inserted_dataset_version_no == 5


def test_create_export_job_rejects_invalid_format(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=_conversation_row(uuid4()),
        message_rows=[],
        export_job_row=None,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ValueError):
        repository.create_export_job(
            CreateExportJobInput(
                tenant_id=uuid4(),
                workspace_id=uuid4(),
                conversation_id=uuid4(),
                export_format="parquet",
                requested_by_user_id=None,
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


def test_create_export_job_raises_when_conversation_missing(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ConversationForExportNotFoundError):
        repository.create_export_job(
            CreateExportJobInput(
                tenant_id=uuid4(),
                workspace_id=uuid4(),
                conversation_id=uuid4(),
                export_format="jsonl",
                requested_by_user_id=None,
            )
        )

    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1


def test_get_export_job_returns_record(tmp_path: Path) -> None:
    job_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 40, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=(
            job_id,
            uuid4(),
            uuid4(),
            uuid4(),
            "jsonl",
            "completed",
            str(tmp_path / "out.jsonl"),
            2,
            {"schema_version": "dataset.v1"},
            None,
            ts,
            ts,
        ),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    result = repository.get_export_job(job_id)

    assert result.job_id == job_id
    assert result.status == "completed"
    assert result.row_count == 2


def test_get_export_job_raises_when_missing(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ExportJobNotFoundError):
        repository.get_export_job(uuid4())


def test_get_conversation_scope_returns_scope(tmp_path: Path) -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        conversation_scope_row=(tenant_id, workspace_id),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    scope = repository.get_conversation_scope(uuid4())

    assert scope.tenant_id == tenant_id
    assert scope.workspace_id == workspace_id


def test_get_conversation_scope_raises_when_missing(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        conversation_scope_row=None,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ConversationForExportNotFoundError):
        repository.get_conversation_scope(uuid4())


def test_list_export_jobs_returns_rows(tmp_path: Path) -> None:
    conversation_id = uuid4()
    tenant_id = uuid4()
    workspace_id = uuid4()
    ts_latest = datetime(2026, 2, 27, 21, 44, tzinfo=timezone.utc)
    ts_old = datetime(2026, 2, 27, 21, 43, tzinfo=timezone.utc)
    rows = [
        (
            uuid4(),
            tenant_id,
            workspace_id,
            conversation_id,
            "jsonl",
            "completed",
            str(tmp_path / "latest.jsonl"),
            3,
            {"schema_version": "dataset.v1"},
            None,
            ts_latest,
            ts_latest,
        ),
        (
            uuid4(),
            tenant_id,
            workspace_id,
            conversation_id,
            "csv",
            "completed",
            str(tmp_path / "old.csv"),
            2,
            {"schema_version": "dataset.v1"},
            None,
            ts_old,
            ts_old,
        ),
    ]
    repository = ExportRepository(
        connection=FakeExportJobListConnection(rows),
        export_root=tmp_path,
    )

    result = repository.list_export_jobs(conversation_id=conversation_id, limit=20)

    assert len(result) == 2
    assert result[0].conversation_id == conversation_id
    assert result[0].row_count == 3
    assert result[1].export_format == "csv"


def test_list_export_jobs_invalid_limit(tmp_path: Path) -> None:
    repository = ExportRepository(
        connection=FakeExportJobListConnection([]),
        export_root=tmp_path,
    )

    with pytest.raises(ValueError):
        repository.list_export_jobs(conversation_id=uuid4(), limit=0)


def test_list_export_jobs_applies_cursor_filter(tmp_path: Path) -> None:
    conversation_id = uuid4()
    before_created_at = datetime(2026, 2, 27, 21, 44, tzinfo=timezone.utc)
    before_job_id = uuid4()
    connection = FakeExportJobListConnection([])
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    repository.list_export_jobs(
        conversation_id=conversation_id,
        limit=10,
        before_created_at=before_created_at,
        before_job_id=before_job_id,
    )

    assert connection.last_sql is not None
    assert "created_at < %s or (created_at = %s and id < %s)" in connection.last_sql
    assert connection.last_params is not None
    assert connection.last_params[1] == before_created_at
    assert connection.last_params[2] == before_created_at
    assert connection.last_params[3] == str(before_job_id)


def test_list_export_jobs_rejects_partial_cursor(tmp_path: Path) -> None:
    repository = ExportRepository(
        connection=FakeExportJobListConnection([]),
        export_root=tmp_path,
    )

    with pytest.raises(ValueError):
        repository.list_export_jobs(
            conversation_id=uuid4(),
            limit=10,
            before_created_at=datetime(2026, 2, 27, 21, 44, tzinfo=timezone.utc),
            before_job_id=None,
        )


def test_list_dataset_versions_returns_ordered_rows(tmp_path: Path) -> None:
    conversation_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 45, tzinfo=timezone.utc)
    rows = [
        (
            uuid4(),
            conversation_id,
            3,
            uuid4(),
            "jsonl",
            str(tmp_path / "v3.jsonl"),
            10,
            {"dataset_version_no": 3},
            ts,
        ),
        (
            uuid4(),
            conversation_id,
            2,
            uuid4(),
            "csv",
            str(tmp_path / "v2.csv"),
            9,
            {"dataset_version_no": 2},
            ts,
        ),
    ]
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
        dataset_version_rows=rows,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    result = repository.list_dataset_versions(conversation_id=conversation_id, limit=20)

    assert len(result) == 2
    assert result[0].version_no == 3
    assert result[1].version_no == 2
    assert result[0].manifest["dataset_version_no"] == 3


def test_list_dataset_versions_rejects_invalid_limit(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ValueError):
        repository.list_dataset_versions(conversation_id=uuid4(), limit=0)


def test_get_dataset_version_returns_specific_row(tmp_path: Path) -> None:
    conversation_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 46, tzinfo=timezone.utc)
    row_v3 = (
        uuid4(),
        conversation_id,
        3,
        uuid4(),
        "jsonl",
        str(tmp_path / "v3.jsonl"),
        10,
        {"dataset_version_no": 3},
        ts,
    )
    row_v2 = (
        uuid4(),
        conversation_id,
        2,
        uuid4(),
        "csv",
        str(tmp_path / "v2.csv"),
        9,
        {"dataset_version_no": 2},
        ts,
    )
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
        dataset_version_rows=[row_v3, row_v2],
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    record = repository.get_dataset_version(conversation_id=conversation_id, version_no=2)

    assert record.version_no == 2
    assert record.export_format == "csv"


def test_get_dataset_version_raises_when_missing(tmp_path: Path) -> None:
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
        dataset_version_rows=[],
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(DatasetVersionNotFoundError):
        repository.get_dataset_version(conversation_id=uuid4(), version_no=1)


def test_get_latest_dataset_version_returns_top_row(tmp_path: Path) -> None:
    conversation_id = uuid4()
    ts = datetime(2026, 2, 27, 21, 47, tzinfo=timezone.utc)
    row_v4 = (
        uuid4(),
        conversation_id,
        4,
        uuid4(),
        "jsonl",
        str(tmp_path / "v4.jsonl"),
        11,
        {"dataset_version_no": 4},
        ts,
    )
    row_v3 = (
        uuid4(),
        conversation_id,
        3,
        uuid4(),
        "csv",
        str(tmp_path / "v3.csv"),
        10,
        {"dataset_version_no": 3},
        ts,
    )
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
        dataset_version_rows=[row_v4, row_v3],
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    record = repository.get_latest_dataset_version(conversation_id=conversation_id)

    assert record.version_no == 4
    assert record.manifest["dataset_version_no"] == 4


def test_read_dataset_version_artifact_uses_latest_when_unspecified(tmp_path: Path) -> None:
    conversation_id = uuid4()
    export_path = tmp_path / "v5.jsonl"
    export_path.write_text('{"v":5}\n', encoding="utf-8")
    ts = datetime(2026, 2, 27, 21, 48, tzinfo=timezone.utc)
    row_v5 = (
        uuid4(),
        conversation_id,
        5,
        uuid4(),
        "jsonl",
        str(export_path),
        12,
        {"dataset_version_no": 5},
        ts,
    )
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=None,
        dataset_version_rows=[row_v5],
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    record, content = repository.read_dataset_version_artifact(
        conversation_id=conversation_id,
        version_no=None,
    )

    assert record.version_no == 5
    assert content == b'{"v":5}\n'


def test_read_export_artifact_returns_bytes(tmp_path: Path) -> None:
    job_id = uuid4()
    export_path = tmp_path / "artifact.jsonl"
    export_path.write_text('{"a":1}\n', encoding="utf-8")
    ts = datetime(2026, 2, 27, 21, 41, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=(
            job_id,
            uuid4(),
            uuid4(),
            uuid4(),
            "jsonl",
            "completed",
            str(export_path),
            1,
            {"schema_version": "dataset.v1"},
            None,
            ts,
            ts,
        ),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    record, content = repository.read_export_artifact(job_id)

    assert record.job_id == job_id
    assert content == b'{"a":1}\n'


def test_read_export_artifact_raises_when_missing_file(tmp_path: Path) -> None:
    job_id = uuid4()
    export_path = tmp_path / "missing.jsonl"
    ts = datetime(2026, 2, 27, 21, 42, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=(
            job_id,
            uuid4(),
            uuid4(),
            uuid4(),
            "jsonl",
            "completed",
            str(export_path),
            1,
            {"schema_version": "dataset.v1"},
            None,
            ts,
            ts,
        ),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(ExportArtifactNotFoundError):
        repository.read_export_artifact(job_id)


def test_read_export_artifact_rejects_path_outside_root(tmp_path: Path) -> None:
    job_id = uuid4()
    outside = Path("/tmp/outside-export.csv")
    ts = datetime(2026, 2, 27, 21, 43, tzinfo=timezone.utc)
    connection = FakeConnection(
        conversation_row=None,
        message_rows=[],
        export_job_row=(
            job_id,
            uuid4(),
            uuid4(),
            uuid4(),
            "csv",
            "completed",
            str(outside),
            1,
            {"schema_version": "dataset.v1"},
            None,
            ts,
            ts,
        ),
    )
    repository = ExportRepository(connection=connection, export_root=tmp_path)

    with pytest.raises(InvalidExportStorageKeyError):
        repository.read_export_artifact(job_id)
