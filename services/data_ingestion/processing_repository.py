"""DB repository used by ingestion processing worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SourceDocumentRecord:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    source_type: str
    original_filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str
    storage_provider: str
    storage_key: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourceChunkInput:
    chunk_index: int
    char_start: int
    char_end: int
    content_text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourceChunkRecord:
    id: UUID
    source_document_id: UUID
    chunk_index: int
    content_text: str


@dataclass(frozen=True)
class ChunkEmbeddingInput:
    source_chunk_id: UUID
    embedding_vector: list[float]
    embedding_model: str
    embedding_dim: int


@dataclass(frozen=True)
class SourceChunkEmbeddingRecord:
    source_chunk_id: UUID
    chunk_index: int
    content_text: str
    embedding_vector: list[float]


@dataclass(frozen=True)
class TopicInput:
    id: UUID
    source_document_id: UUID
    label: str
    summary: str
    cluster_key: str
    centroid_vector: list[float]
    chunk_count: int


@dataclass(frozen=True)
class SourceChunkTopicInput:
    source_chunk_id: UUID
    topic_id: UUID
    relevance_score: float
    link_type: str


class IngestionProcessingRepository:
    def __init__(self, connection: Any):
        self._connection = connection

    def get_source_document(self, source_id: UUID) -> SourceDocumentRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    tenant_id,
                    workspace_id,
                    source_type,
                    original_filename,
                    content_type,
                    byte_size,
                    checksum_sha256,
                    storage_provider,
                    storage_key,
                    metadata
                FROM source_document
                WHERE id = %s
                """,
                (str(source_id),),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return SourceDocumentRecord(
            id=row[0],
            tenant_id=row[1],
            workspace_id=row[2],
            source_type=row[3],
            original_filename=row[4],
            content_type=row[5],
            byte_size=int(row[6]),
            checksum_sha256=row[7],
            storage_provider=row[8],
            storage_key=row[9],
            metadata=row[10] if row[10] is not None else {},
        )

    def replace_source_chunks(self, source_id: UUID, chunks: list[SourceChunkInput]) -> int:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM source_chunk WHERE source_document_id = %s",
                    (str(source_id),),
                )
                for chunk in chunks:
                    cursor.execute(
                        """
                        INSERT INTO source_chunk (
                            source_document_id,
                            chunk_index,
                            char_start,
                            char_end,
                            content_text,
                            metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            str(source_id),
                            chunk.chunk_index,
                            chunk.char_start,
                            chunk.char_end,
                            chunk.content_text,
                            json.dumps(chunk.metadata),
                        ),
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return len(chunks)

    def get_source_chunks(self, source_id: UUID) -> list[SourceChunkRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, source_document_id, chunk_index, content_text
                FROM source_chunk
                WHERE source_document_id = %s
                ORDER BY chunk_index ASC
                """,
                (str(source_id),),
            )
            rows = cursor.fetchall()
        return [
            SourceChunkRecord(
                id=row[0],
                source_document_id=row[1],
                chunk_index=int(row[2]),
                content_text=row[3],
            )
            for row in rows
        ]

    def upsert_chunk_embeddings(self, embeddings: list[ChunkEmbeddingInput]) -> int:
        try:
            with self._connection.cursor() as cursor:
                for item in embeddings:
                    cursor.execute(
                        """
                        INSERT INTO source_chunk_embedding (
                            source_chunk_id,
                            embedding,
                            embedding_model,
                            embedding_dim
                        )
                        VALUES (%s, %s::vector, %s, %s)
                        ON CONFLICT (source_chunk_id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            embedding_model = EXCLUDED.embedding_model,
                            embedding_dim = EXCLUDED.embedding_dim,
                            updated_at = NOW()
                        """,
                        (
                            str(item.source_chunk_id),
                            self._to_vector_literal(item.embedding_vector),
                            item.embedding_model,
                            item.embedding_dim,
                        ),
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return len(embeddings)

    def get_source_chunk_embeddings(
        self, source_id: UUID
    ) -> list[SourceChunkEmbeddingRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    sc.id,
                    sc.chunk_index,
                    sc.content_text,
                    sce.embedding
                FROM source_chunk sc
                JOIN source_chunk_embedding sce ON sc.id = sce.source_chunk_id
                WHERE sc.source_document_id = %s
                ORDER BY sc.chunk_index ASC
                """,
                (str(source_id),),
            )
            rows = cursor.fetchall()
        return [
            SourceChunkEmbeddingRecord(
                source_chunk_id=row[0],
                chunk_index=int(row[1]),
                content_text=row[2],
                embedding_vector=self._parse_vector(row[3]),
            )
            for row in rows
        ]

    def replace_topics_for_source(
        self,
        *,
        source_id: UUID,
        topics: list[TopicInput],
        chunk_links: list[SourceChunkTopicInput],
    ) -> tuple[int, int]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM topic WHERE source_document_id = %s",
                    (str(source_id),),
                )
                for topic in topics:
                    cursor.execute(
                        """
                        INSERT INTO topic (
                            id,
                            source_document_id,
                            label,
                            summary,
                            cluster_key,
                            centroid,
                            chunk_count
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                        """,
                        (
                            str(topic.id),
                            str(topic.source_document_id),
                            topic.label,
                            topic.summary,
                            topic.cluster_key,
                            self._to_vector_literal(topic.centroid_vector),
                            topic.chunk_count,
                        ),
                    )
                for link in chunk_links:
                    cursor.execute(
                        """
                        INSERT INTO source_chunk_topic (
                            source_chunk_id,
                            topic_id,
                            relevance_score,
                            link_type
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            str(link.source_chunk_id),
                            str(link.topic_id),
                            link.relevance_score,
                            link.link_type,
                        ),
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return len(topics), len(chunk_links)

    def enqueue_dlq(
        self,
        *,
        source_document_id: UUID | None,
        error_type: str,
        error_message: str,
        payload: dict[str, Any],
        retryable: bool,
    ) -> int:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingestion_dlq (
                        source_document_id,
                        error_type,
                        error_message,
                        payload,
                        retryable
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        str(source_document_id) if source_document_id else None,
                        error_type,
                        error_message,
                        json.dumps(payload),
                        retryable,
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - defensive guard
                    raise RuntimeError("DLQ insert did not return a row")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return int(row[0])

    @staticmethod
    def _to_vector_literal(embedding_vector: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in embedding_vector) + "]"

    @staticmethod
    def _parse_vector(value: Any) -> list[float]:
        if isinstance(value, list):
            return [float(item) for item in value]
        if isinstance(value, tuple):
            return [float(item) for item in value]
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("[") and text.endswith("]"):
                inner = text[1:-1].strip()
                if not inner:
                    return []
                return [float(part.strip()) for part in inner.split(",")]
        raise ValueError(f"Unsupported vector value type: {type(value)}")
