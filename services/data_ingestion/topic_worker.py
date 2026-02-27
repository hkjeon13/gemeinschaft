"""Topic clustering worker from embedded source chunks."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from services.data_ingestion.ingestion_worker import SourceNotFoundError
from services.data_ingestion.processing_repository import (
    IngestionProcessingRepository,
    SourceChunkEmbeddingRecord,
    SourceChunkTopicInput,
    TopicInput,
)

TOKEN_RE = re.compile(r"[A-Za-z0-9]{3,}")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "are",
    "was",
    "were",
    "you",
    "your",
    "have",
    "has",
    "into",
    "about",
    "not",
    "but",
    "can",
}


@dataclass(frozen=True)
class TopicSourceResult:
    source_id: UUID
    status: str
    topic_count: int
    link_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class _Cluster:
    members: list[SourceChunkEmbeddingRecord] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if len(vector_a) != len(vector_b):
        raise ValueError("Embedding vectors must have same dimension")
    dot = sum(left * right for left, right in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(value * value for value in vector_a))
    norm_b = math.sqrt(sum(value * value for value in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise ValueError("All embeddings in a cluster must have same dimension")

    summed = [0.0] * dimension
    for vector in vectors:
        for index, value in enumerate(vector):
            summed[index] += value
    return [value / len(vectors) for value in summed]


def _build_label(texts: list[str], fallback_index: int) -> str:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in TOKEN_RE.findall(text.lower()):
            if token not in STOPWORDS:
                counter[token] += 1
    if not counter:
        return f"Topic {fallback_index}"
    terms = [term for term, _count in counter.most_common(3)]
    return " / ".join(term.capitalize() for term in terms)


def _build_summary(texts: list[str], max_chars: int = 220) -> str:
    cleaned = []
    for text in texts:
        normalized = " ".join(text.strip().split())
        if normalized:
            cleaned.append(normalized)
    summary = " ".join(cleaned)
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


class TopicWorker:
    def __init__(
        self,
        *,
        repository: IngestionProcessingRepository,
        similarity_threshold: float,
        embedding_model: str,
        embedding_dim: int,
    ):
        self._repository = repository
        self._similarity_threshold = similarity_threshold
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim

    def cluster_source(self, source_id: UUID) -> TopicSourceResult:
        source = self._repository.get_source_document(source_id)
        if source is None:
            raise SourceNotFoundError(f"Source document {source_id} not found")

        try:
            chunk_embeddings = self._repository.get_source_chunk_embeddings(source_id)
            if not chunk_embeddings:
                return TopicSourceResult(
                    source_id=source_id,
                    status="no_embeddings",
                    topic_count=0,
                    link_count=0,
                )

            clusters: list[_Cluster] = []
            for record in chunk_embeddings:
                if len(record.embedding_vector) != self._embedding_dim:
                    raise ValueError(
                        f"Invalid embedding dimension for chunk {record.source_chunk_id}: "
                        f"expected {self._embedding_dim}, got {len(record.embedding_vector)}"
                    )

                best_index = -1
                best_similarity = -1.0
                for index, cluster in enumerate(clusters):
                    similarity = _cosine_similarity(record.embedding_vector, cluster.centroid)
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_index = index

                if best_index >= 0 and best_similarity >= self._similarity_threshold:
                    clusters[best_index].members.append(record)
                    clusters[best_index].centroid = _mean_vector(
                        [member.embedding_vector for member in clusters[best_index].members]
                    )
                else:
                    clusters.append(
                        _Cluster(
                            members=[record],
                            centroid=list(record.embedding_vector),
                        )
                    )

            topics: list[TopicInput] = []
            links: list[SourceChunkTopicInput] = []
            for index, cluster in enumerate(clusters, start=1):
                topic_id = uuid4()
                texts = [member.content_text for member in cluster.members]
                topics.append(
                    TopicInput(
                        id=topic_id,
                        source_document_id=source_id,
                        label=_build_label(texts, fallback_index=index),
                        summary=_build_summary(texts),
                        cluster_key=f"topic-{index:03d}",
                        centroid_vector=cluster.centroid,
                        chunk_count=len(cluster.members),
                    )
                )
                for member in cluster.members:
                    links.append(
                        SourceChunkTopicInput(
                            source_chunk_id=member.source_chunk_id,
                            topic_id=topic_id,
                            relevance_score=_cosine_similarity(
                                member.embedding_vector, cluster.centroid
                            ),
                            link_type="primary",
                        )
                    )

            topic_count, link_count = self._repository.replace_topics_for_source(
                source_id=source_id,
                topics=topics,
                chunk_links=links,
            )
            return TopicSourceResult(
                source_id=source_id,
                status="clustered",
                topic_count=topic_count,
                link_count=link_count,
            )
        except Exception as exc:
            dlq_id = self._repository.enqueue_dlq(
                source_document_id=source_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                payload={
                    "worker": "topic_worker",
                    "similarity_threshold": self._similarity_threshold,
                    "embedding_model": self._embedding_model,
                    "embedding_dim": self._embedding_dim,
                },
                retryable=False,
            )
            return TopicSourceResult(
                source_id=source_id,
                status="dlq",
                topic_count=0,
                link_count=0,
                dlq_id=dlq_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
