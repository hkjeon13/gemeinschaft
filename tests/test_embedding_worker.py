"""Tests for embedding worker and deterministic embedding generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from services.data_ingestion.embedding_worker import EmbeddingWorker, generate_embedding
from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.processing_repository import (
    ChunkEmbeddingInput,
    SourceChunkRecord,
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
    def __init__(
        self,
        *,
        source_record: SourceDocumentRecord | None,
        chunks: list[SourceChunkRecord],
        fail_on_upsert: bool = False,
    ):
        self.source_record = source_record
        self.chunks = chunks
        self.fail_on_upsert = fail_on_upsert
        self.last_embeddings: list[ChunkEmbeddingInput] = []
        self.dlq_records: list[DlqRecord] = []

    def get_source_document(self, source_id: Any) -> SourceDocumentRecord | None:
        if self.source_record is None:
            return None
        if self.source_record.id != source_id:
            return None
        return self.source_record

    def get_source_chunks(self, source_id: Any) -> list[SourceChunkRecord]:
        if self.source_record is None:
            return []
        if self.source_record.id != source_id:
            return []
        return self.chunks

    def upsert_chunk_embeddings(self, embeddings: list[ChunkEmbeddingInput]) -> int:
        if self.fail_on_upsert:
            raise RuntimeError("embedding upsert failed")
        self.last_embeddings = embeddings
        return len(embeddings)

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
        return 91


def _source_record() -> SourceDocumentRecord:
    return SourceDocumentRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        source_type="upload",
        original_filename="doc.txt",
        content_type="text/plain",
        byte_size=100,
        checksum_sha256="abc",
        storage_provider="local_fs",
        storage_key="k/doc.txt",
        metadata={},
    )


def test_generate_embedding_is_deterministic() -> None:
    first = generate_embedding("hello world", dim=8)
    second = generate_embedding("hello world", dim=8)
    assert first == second
    assert len(first) == 8


def test_generate_embedding_rejects_invalid_dim() -> None:
    with pytest.raises(ValueError):
        generate_embedding("hello", dim=0)


def test_embedding_worker_embeds_all_chunks() -> None:
    source_record = _source_record()
    repository = FakeRepository(
        source_record=source_record,
        chunks=[
            SourceChunkRecord(
                id=uuid4(),
                source_document_id=source_record.id,
                chunk_index=0,
                content_text="first chunk",
            ),
            SourceChunkRecord(
                id=uuid4(),
                source_document_id=source_record.id,
                chunk_index=1,
                content_text="second chunk",
            ),
        ],
    )
    worker = EmbeddingWorker(
        repository=repository,
        embedding_model="hash-v1",
        embedding_dim=16,
    )

    result = worker.embed_source(source_record.id)

    assert result.status == "embedded"
    assert result.chunk_count == 2
    assert result.embedding_count == 2
    assert len(repository.last_embeddings) == 2
    assert repository.last_embeddings[0].embedding_model == "hash-v1"
    assert repository.last_embeddings[0].embedding_dim == 16
    assert len(repository.last_embeddings[0].embedding_vector) == 16
    assert repository.dlq_records == []


def test_embedding_worker_returns_no_chunks_when_empty() -> None:
    source_record = _source_record()
    repository = FakeRepository(source_record=source_record, chunks=[])
    worker = EmbeddingWorker(
        repository=repository,
        embedding_model="hash-v1",
        embedding_dim=8,
    )

    result = worker.embed_source(source_record.id)

    assert result.status == "no_chunks"
    assert result.embedding_count == 0
    assert repository.last_embeddings == []


def test_embedding_worker_sends_failures_to_dlq() -> None:
    source_record = _source_record()
    repository = FakeRepository(
        source_record=source_record,
        chunks=[
            SourceChunkRecord(
                id=uuid4(),
                source_document_id=source_record.id,
                chunk_index=0,
                content_text="chunk",
            )
        ],
        fail_on_upsert=True,
    )
    worker = EmbeddingWorker(
        repository=repository,
        embedding_model="hash-v1",
        embedding_dim=8,
    )

    result = worker.embed_source(source_record.id)

    assert result.status == "dlq"
    assert result.dlq_id == 91
    assert result.error_type == "RuntimeError"
    assert len(repository.dlq_records) == 1


def test_embedding_worker_raises_for_missing_source() -> None:
    repository = FakeRepository(source_record=None, chunks=[])
    worker = EmbeddingWorker(
        repository=repository,
        embedding_model="hash-v1",
        embedding_dim=8,
    )

    with pytest.raises(SourceNotFoundError):
        worker.embed_source(uuid4())
