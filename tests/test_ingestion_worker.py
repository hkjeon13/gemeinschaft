"""Tests for ingestion parsing/chunking worker and DLQ behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from services.data_ingestion.ingestion_worker import (
    IngestionWorker,
    SourceNotFoundError,
    chunk_text,
)
from services.data_ingestion.processing_repository import (
    SourceChunkInput,
    SourceDocumentRecord,
)


@dataclass
class DlqRecord:
    source_document_id: Any
    error_type: str
    error_message: str
    payload: dict[str, Any]
    retryable: bool


class FakeRepository:
    def __init__(self, source: SourceDocumentRecord | None):
        self.source = source
        self.replaced_chunks: list[SourceChunkInput] = []
        self.dlq_records: list[DlqRecord] = []
        self.next_dlq_id = 1

    def get_source_document(self, source_id: Any) -> SourceDocumentRecord | None:
        if self.source is None:
            return None
        if self.source.id != source_id:
            return None
        return self.source

    def replace_source_chunks(self, source_id: Any, chunks: list[SourceChunkInput]) -> int:
        assert self.source is not None
        assert source_id == self.source.id
        self.replaced_chunks = chunks
        return len(chunks)

    def enqueue_dlq(
        self,
        *,
        source_document_id: Any,
        error_type: str,
        error_message: str,
        payload: dict[str, Any],
        retryable: bool,
    ) -> int:
        self.dlq_records.append(
            DlqRecord(
                source_document_id=source_document_id,
                error_type=error_type,
                error_message=error_message,
                payload=payload,
                retryable=retryable,
            )
        )
        dlq_id = self.next_dlq_id
        self.next_dlq_id += 1
        return dlq_id


class FakeStorage:
    provider = "local_fs"

    def __init__(self, content_by_key: dict[str, bytes]):
        self.content_by_key = content_by_key

    def get_object(self, key: str) -> bytes:
        if key not in self.content_by_key:
            raise FileNotFoundError(key)
        return self.content_by_key[key]

    def put_object(self, key: str, data: bytes, content_type: str | None = None) -> None:
        del content_type
        self.content_by_key[key] = data

    def delete_object(self, key: str) -> None:
        self.content_by_key.pop(key, None)


def _source_record(content_type: str, filename: str, storage_key: str) -> SourceDocumentRecord:
    return SourceDocumentRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        source_type="upload",
        original_filename=filename,
        content_type=content_type,
        byte_size=0,
        checksum_sha256="",
        storage_provider="local_fs",
        storage_key=storage_key,
        metadata={},
    )


def test_chunk_text_splits_with_overlap() -> None:
    chunks = chunk_text("abcdefghij", max_chars=4, overlap_chars=1)
    assert [chunk.content_text for chunk in chunks] == ["abcd", "defg", "ghij"]
    assert [chunk.char_start for chunk in chunks] == [0, 3, 6]
    assert [chunk.char_end for chunk in chunks] == [4, 7, 10]


def test_worker_processes_text_source_successfully() -> None:
    record = _source_record("text/plain", "doc.txt", "k/doc.txt")
    repository = FakeRepository(source=record)
    storage = FakeStorage({"k/doc.txt": b"hello world\nthis is a test source"})
    worker = IngestionWorker(
        repository=repository,
        storage=storage,
        max_chunk_chars=12,
        overlap_chars=2,
    )

    result = worker.process_source(record.id)

    assert result.status == "processed"
    assert result.chunk_count >= 2
    assert result.dlq_id is None
    assert repository.dlq_records == []
    assert len(repository.replaced_chunks) == result.chunk_count


def test_worker_sends_unsupported_source_to_dlq() -> None:
    record = _source_record("application/pdf", "manual.pdf", "k/manual.pdf")
    repository = FakeRepository(source=record)
    storage = FakeStorage({"k/manual.pdf": b"%PDF-1.4"})
    worker = IngestionWorker(
        repository=repository,
        storage=storage,
        max_chunk_chars=100,
        overlap_chars=10,
    )

    result = worker.process_source(record.id)

    assert result.status == "dlq"
    assert result.chunk_count == 0
    assert result.dlq_id == 1
    assert result.error_type == "UnsupportedSourceError"
    assert len(repository.dlq_records) == 1
    assert repository.dlq_records[0].retryable is False


def test_worker_raises_for_missing_source() -> None:
    repository = FakeRepository(source=None)
    storage = FakeStorage({})
    worker = IngestionWorker(
        repository=repository,
        storage=storage,
        max_chunk_chars=100,
        overlap_chars=10,
    )

    with pytest.raises(SourceNotFoundError):
        worker.process_source(uuid4())
