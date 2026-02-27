"""Tests for topic clustering worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.processing_repository import (
    SourceChunkEmbeddingRecord,
    SourceChunkTopicInput,
    SourceDocumentRecord,
    TopicInput,
)
from services.data_ingestion.topic_worker import TopicWorker


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
        chunk_embeddings: list[SourceChunkEmbeddingRecord],
        fail_on_replace: bool = False,
    ):
        self.source_record = source_record
        self.chunk_embeddings = chunk_embeddings
        self.fail_on_replace = fail_on_replace
        self.topics_saved: list[TopicInput] = []
        self.links_saved: list[SourceChunkTopicInput] = []
        self.dlq_records: list[DlqRecord] = []

    def get_source_document(self, source_id: Any) -> SourceDocumentRecord | None:
        if self.source_record is None:
            return None
        if self.source_record.id != source_id:
            return None
        return self.source_record

    def get_source_chunk_embeddings(
        self, source_id: Any
    ) -> list[SourceChunkEmbeddingRecord]:
        if self.source_record is None:
            return []
        if self.source_record.id != source_id:
            return []
        return self.chunk_embeddings

    def replace_topics_for_source(
        self,
        *,
        source_id: Any,
        topics: list[TopicInput],
        chunk_links: list[SourceChunkTopicInput],
    ) -> tuple[int, int]:
        assert self.source_record is not None
        assert source_id == self.source_record.id
        if self.fail_on_replace:
            raise RuntimeError("replace topics failed")
        self.topics_saved = topics
        self.links_saved = chunk_links
        return len(topics), len(chunk_links)

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
        return 301


def _source_record() -> SourceDocumentRecord:
    return SourceDocumentRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        source_type="upload",
        original_filename="source.txt",
        content_type="text/plain",
        byte_size=100,
        checksum_sha256="abc",
        storage_provider="local_fs",
        storage_key="k/source.txt",
        metadata={},
    )


def test_topic_worker_clusters_embeddings_and_persists_topics() -> None:
    source_record = _source_record()
    chunk_embeddings = [
        SourceChunkEmbeddingRecord(
            source_chunk_id=uuid4(),
            chunk_index=0,
            content_text="Payment refund policy for premium users",
            embedding_vector=[1.0, 0.0, 0.0, 0.0],
        ),
        SourceChunkEmbeddingRecord(
            source_chunk_id=uuid4(),
            chunk_index=1,
            content_text="Refund process and payment status details",
            embedding_vector=[0.95, 0.1, 0.0, 0.0],
        ),
        SourceChunkEmbeddingRecord(
            source_chunk_id=uuid4(),
            chunk_index=2,
            content_text="Warehouse inventory and shipping timelines",
            embedding_vector=[0.0, 1.0, 0.0, 0.0],
        ),
    ]
    repository = FakeRepository(
        source_record=source_record,
        chunk_embeddings=chunk_embeddings,
    )
    worker = TopicWorker(
        repository=repository,
        similarity_threshold=0.9,
        embedding_model="hash-v1",
        embedding_dim=4,
    )

    result = worker.cluster_source(source_record.id)

    assert result.status == "clustered"
    assert result.topic_count == 2
    assert result.link_count == 3
    assert len(repository.topics_saved) == 2
    assert len(repository.links_saved) == 3
    assert repository.topics_saved[0].cluster_key == "topic-001"
    assert repository.topics_saved[1].cluster_key == "topic-002"
    assert repository.dlq_records == []


def test_topic_worker_returns_no_embeddings_when_absent() -> None:
    source_record = _source_record()
    repository = FakeRepository(source_record=source_record, chunk_embeddings=[])
    worker = TopicWorker(
        repository=repository,
        similarity_threshold=0.8,
        embedding_model="hash-v1",
        embedding_dim=4,
    )

    result = worker.cluster_source(source_record.id)

    assert result.status == "no_embeddings"
    assert result.topic_count == 0
    assert result.link_count == 0
    assert repository.topics_saved == []


def test_topic_worker_writes_dlq_on_failure() -> None:
    source_record = _source_record()
    repository = FakeRepository(
        source_record=source_record,
        chunk_embeddings=[
            SourceChunkEmbeddingRecord(
                source_chunk_id=uuid4(),
                chunk_index=0,
                content_text="A chunk",
                embedding_vector=[1.0, 0.0, 0.0, 0.0],
            )
        ],
        fail_on_replace=True,
    )
    worker = TopicWorker(
        repository=repository,
        similarity_threshold=0.8,
        embedding_model="hash-v1",
        embedding_dim=4,
    )

    result = worker.cluster_source(source_record.id)

    assert result.status == "dlq"
    assert result.dlq_id == 301
    assert result.error_type == "RuntimeError"
    assert len(repository.dlq_records) == 1


def test_topic_worker_raises_for_missing_source() -> None:
    repository = FakeRepository(source_record=None, chunk_embeddings=[])
    worker = TopicWorker(
        repository=repository,
        similarity_threshold=0.8,
        embedding_model="hash-v1",
        embedding_dim=4,
    )

    with pytest.raises(SourceNotFoundError):
        worker.cluster_source(uuid4())
