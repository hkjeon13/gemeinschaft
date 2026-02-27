"""Embedding worker for source chunks using deterministic local embeddings."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from uuid import UUID

from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.processing_repository import (
    ChunkEmbeddingInput,
    IngestionProcessingRepository,
)


@dataclass(frozen=True)
class EmbedSourceResult:
    source_id: UUID
    status: str
    chunk_count: int
    embedding_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


def generate_embedding(text: str, *, dim: int) -> list[float]:
    if dim < 1:
        raise ValueError("dim must be >= 1")

    seed = hashlib.sha256(text.encode("utf-8")).digest()
    required_bytes = dim * 2
    buffer = bytearray()
    counter = 0

    while len(buffer) < required_bytes:
        round_hash = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        buffer.extend(round_hash)
        counter += 1

    values: list[float] = []
    for index in range(dim):
        start = index * 2
        raw = int.from_bytes(buffer[start : start + 2], "big")
        value = (raw / 65535.0) * 2.0 - 1.0
        values.append(value)

    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


class EmbeddingWorker:
    def __init__(
        self,
        *,
        repository: IngestionProcessingRepository,
        embedding_model: str,
        embedding_dim: int,
    ):
        self._repository = repository
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim

    def embed_source(self, source_id: UUID) -> EmbedSourceResult:
        record = self._repository.get_source_document(source_id)
        if record is None:
            raise SourceNotFoundError(f"Source document {source_id} not found")

        try:
            chunks = self._repository.get_source_chunks(source_id)
            if not chunks:
                return EmbedSourceResult(
                    source_id=source_id,
                    status="no_chunks",
                    chunk_count=0,
                    embedding_count=0,
                )

            embeddings = [
                ChunkEmbeddingInput(
                    source_chunk_id=chunk.id,
                    embedding_vector=generate_embedding(
                        chunk.content_text, dim=self._embedding_dim
                    ),
                    embedding_model=self._embedding_model,
                    embedding_dim=self._embedding_dim,
                )
                for chunk in chunks
            ]
            embedding_count = self._repository.upsert_chunk_embeddings(embeddings)
            return EmbedSourceResult(
                source_id=source_id,
                status="embedded",
                chunk_count=len(chunks),
                embedding_count=embedding_count,
            )
        except Exception as exc:
            dlq_id = self._repository.enqueue_dlq(
                source_document_id=source_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                payload={
                    "embedding_model": self._embedding_model,
                    "embedding_dim": self._embedding_dim,
                },
                retryable=False,
            )
            return EmbedSourceResult(
                source_id=source_id,
                status="dlq",
                chunk_count=0,
                embedding_count=0,
                dlq_id=dlq_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
