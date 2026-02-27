"""Export repository for building reusable conversation datasets."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class ConversationForExportNotFoundError(RuntimeError):
    """Raised when requested conversation is not found for tenant/workspace."""


class ExportJobNotFoundError(RuntimeError):
    """Raised when export job is missing."""


class ExportArtifactNotFoundError(RuntimeError):
    """Raised when exported dataset file is missing on storage."""


class InvalidExportStorageKeyError(RuntimeError):
    """Raised when export storage key is outside configured export root."""


@dataclass(frozen=True)
class CreateExportJobInput:
    tenant_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    export_format: str
    requested_by_user_id: UUID | None


@dataclass(frozen=True)
class ExportJobRecord:
    job_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    export_format: str
    status: str
    storage_key: str
    row_count: int
    manifest: dict[str, Any]
    requested_by_user_id: UUID | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class DatasetVersionRecord:
    dataset_version_id: UUID
    conversation_id: UUID
    version_no: int
    export_job_id: UUID
    export_format: str
    storage_key: str
    row_count: int
    manifest: dict[str, Any]
    created_at: datetime


class ExportRepository:
    def __init__(self, connection: Any, export_root: Path):
        self._connection = connection
        self._export_root = export_root

    def create_export_job(self, payload: CreateExportJobInput) -> ExportJobRecord:
        export_format = payload.export_format.strip().lower()
        if export_format not in {"jsonl", "csv"}:
            raise ValueError("export_format must be one of: jsonl, csv")

        self._export_root.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection.cursor() as cursor:
                conversation_meta = self._load_conversation_meta(cursor, payload)
                rows = self._load_conversation_rows(cursor, payload.conversation_id)
                dataset_version_no = self._next_dataset_version(
                    cursor=cursor,
                    conversation_id=payload.conversation_id,
                )

                manifest = {
                    "schema_version": "dataset.v1",
                    "conversation": conversation_meta,
                    "row_count": len(rows),
                    "format": export_format,
                    "dataset_version_no": dataset_version_no,
                }

                export_job_id = uuid4()
                export_file = self._export_root / f"{export_job_id}.{export_format}"
                export_file.write_text(
                    self._serialize_rows(rows=rows, export_format=export_format),
                    encoding="utf-8",
                )

                cursor.execute(
                    """
                    INSERT INTO export_job (
                        id,
                        tenant_id,
                        workspace_id,
                        conversation_id,
                        format,
                        status,
                        storage_key,
                        row_count,
                        manifest,
                        requested_by_user_id,
                        completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s::jsonb, %s, NOW())
                    RETURNING id, status, created_at, completed_at
                    """,
                    (
                        str(export_job_id),
                        str(payload.tenant_id),
                        str(payload.workspace_id),
                        str(payload.conversation_id),
                        export_format,
                        str(export_file),
                        len(rows),
                        json.dumps(manifest),
                        str(payload.requested_by_user_id)
                        if payload.requested_by_user_id
                        else None,
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("export_job insert did not return a row")
                self._insert_dataset_version(
                    cursor=cursor,
                    conversation_id=payload.conversation_id,
                    dataset_version_no=dataset_version_no,
                    export_job_id=export_job_id,
                    export_format=export_format,
                    storage_key=export_file,
                    row_count=len(rows),
                    manifest=manifest,
                )

                event_payload = {
                    "export_job_id": str(export_job_id),
                    "dataset_version_no": dataset_version_no,
                    "format": export_format,
                    "storage_key": str(export_file),
                    "row_count": len(rows),
                    "schema_version": manifest["schema_version"],
                }
                self._append_export_completed_event(
                    cursor=cursor,
                    conversation_id=payload.conversation_id,
                    payload=event_payload,
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return ExportJobRecord(
            job_id=row[0],
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            conversation_id=payload.conversation_id,
            export_format=export_format,
            status=row[1],
            storage_key=str(export_file),
            row_count=len(rows),
            manifest=manifest,
            requested_by_user_id=payload.requested_by_user_id,
            created_at=row[2],
            completed_at=row[3],
        )

    def get_export_job(self, export_job_id: UUID) -> ExportJobRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    tenant_id,
                    workspace_id,
                    conversation_id,
                    format,
                    status,
                    storage_key,
                    row_count,
                    manifest,
                    requested_by_user_id,
                    created_at,
                    completed_at
                FROM export_job
                WHERE id = %s
                """,
                (str(export_job_id),),
            )
            row = cursor.fetchone()
            if row is None:
                raise ExportJobNotFoundError(f"Export job {export_job_id} not found")

        return ExportJobRecord(
            job_id=row[0],
            tenant_id=row[1],
            workspace_id=row[2],
            conversation_id=row[3],
            export_format=row[4],
            status=row[5],
            storage_key=row[6],
            row_count=int(row[7]),
            manifest=row[8],
            requested_by_user_id=row[9],
            created_at=row[10],
            completed_at=row[11],
        )

    def read_export_artifact(self, export_job_id: UUID) -> tuple[ExportJobRecord, bytes]:
        record = self.get_export_job(export_job_id)
        artifact_path = Path(record.storage_key)
        root = self._export_root.resolve()
        candidate = artifact_path.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise InvalidExportStorageKeyError(
                f"Export storage key is outside export root: {candidate}"
            ) from exc
        if not candidate.exists():
            raise ExportArtifactNotFoundError(f"Export artifact not found: {candidate}")
        return record, candidate.read_bytes()

    def list_dataset_versions(
        self, *, conversation_id: UUID, limit: int = 20
    ) -> list[DatasetVersionRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    conversation_id,
                    version_no,
                    export_job_id,
                    format,
                    storage_key,
                    row_count,
                    manifest,
                    created_at
                FROM conversation_dataset_version
                WHERE conversation_id = %s
                ORDER BY version_no DESC
                LIMIT %s
                """,
                (str(conversation_id), limit),
            )
            rows = cursor.fetchall()

        return [
            DatasetVersionRecord(
                dataset_version_id=row[0],
                conversation_id=row[1],
                version_no=int(row[2]),
                export_job_id=row[3],
                export_format=row[4],
                storage_key=row[5],
                row_count=int(row[6]),
                manifest=row[7],
                created_at=row[8],
            )
            for row in rows
        ]

    def _load_conversation_meta(
        self, cursor: Any, payload: CreateExportJobInput
    ) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT id, title, objective, start_trigger, status, created_at, started_at, ended_at
            FROM conversation
            WHERE id = %s AND tenant_id = %s AND workspace_id = %s
            FOR UPDATE
            """,
            (
                str(payload.conversation_id),
                str(payload.tenant_id),
                str(payload.workspace_id),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConversationForExportNotFoundError(
                "Conversation not found for the given tenant/workspace"
            )
        return {
            "id": str(row[0]),
            "title": row[1],
            "objective": row[2],
            "start_trigger": row[3],
            "status": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
            "started_at": row[6].isoformat() if row[6] else None,
            "ended_at": row[7].isoformat() if row[7] else None,
        }

    def _load_conversation_rows(self, cursor: Any, conversation_id: UUID) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT
                m.id,
                m.turn_index,
                m.message_type,
                m.status,
                m.content_text,
                m.created_at,
                p.id,
                p.kind,
                p.display_name,
                p.role_label
            FROM message m
            JOIN participant p ON m.participant_id = p.id
            WHERE m.conversation_id = %s
            ORDER BY m.turn_index ASC
            """,
            (str(conversation_id),),
        )
        rows = []
        for row in cursor.fetchall():
            rows.append(
                {
                    "message_id": str(row[0]),
                    "turn_index": int(row[1]),
                    "message_type": row[2],
                    "message_status": row[3],
                    "content_text": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                    "participant_id": str(row[6]),
                    "participant_kind": row[7],
                    "participant_name": row[8],
                    "participant_role": row[9],
                }
            )
        return rows

    def _append_export_completed_event(
        self, *, cursor: Any, conversation_id: UUID, payload: dict[str, Any]
    ) -> None:
        cursor.execute(
            """
            SELECT COALESCE(MAX(seq_no), 0)
            FROM event
            WHERE conversation_id = %s
            """,
            (str(conversation_id),),
        )
        current_seq = int(cursor.fetchone()[0])
        next_seq = current_seq + 1
        cursor.execute(
            """
            INSERT INTO event (
                conversation_id,
                seq_no,
                event_type,
                payload
            )
            VALUES (%s, %s, 'export.completed', %s::jsonb)
            """,
            (
                str(conversation_id),
                next_seq,
                json.dumps(payload),
            ),
        )

    def _next_dataset_version(self, *, cursor: Any, conversation_id: UUID) -> int:
        cursor.execute(
            """
            SELECT COALESCE(MAX(version_no), 0)
            FROM conversation_dataset_version
            WHERE conversation_id = %s
            """,
            (str(conversation_id),),
        )
        return int(cursor.fetchone()[0]) + 1

    def _insert_dataset_version(
        self,
        *,
        cursor: Any,
        conversation_id: UUID,
        dataset_version_no: int,
        export_job_id: UUID,
        export_format: str,
        storage_key: Path,
        row_count: int,
        manifest: dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO conversation_dataset_version (
                conversation_id,
                version_no,
                export_job_id,
                format,
                storage_key,
                row_count,
                manifest
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                str(conversation_id),
                dataset_version_no,
                str(export_job_id),
                export_format,
                str(storage_key),
                row_count,
                json.dumps(manifest),
            ),
        )

    def _serialize_rows(self, *, rows: list[dict[str, Any]], export_format: str) -> str:
        if export_format == "jsonl":
            if not rows:
                return ""
            return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"

        output = io.StringIO()
        fieldnames = [
            "message_id",
            "turn_index",
            "message_type",
            "message_status",
            "content_text",
            "created_at",
            "participant_id",
            "participant_kind",
            "participant_name",
            "participant_role",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return output.getvalue()
