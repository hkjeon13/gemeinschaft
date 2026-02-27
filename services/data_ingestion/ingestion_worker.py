"""Ingestion worker: load source, parse text, chunk, persist or DLQ."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from services.data_ingestion.object_storage import ObjectStorage
from services.data_ingestion.processing_repository import (
    IngestionProcessingRepository,
    SourceChunkInput,
    SourceDocumentRecord,
)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json"}
JSON_CONTENT_TYPES = {"application/json", "application/ld+json"}
TEXT_CONTENT_TYPE_PREFIX = "text/"


class SourceNotFoundError(RuntimeError):
    """Raised when a source_document row is not found."""


class SourceParseError(RuntimeError):
    """Raised when source bytes cannot be parsed into text."""


class UnsupportedSourceError(SourceParseError):
    """Raised when this worker cannot parse the source content type."""


@dataclass(frozen=True)
class ProcessSourceResult:
    source_id: UUID
    status: str
    chunk_count: int
    dlq_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


def _decode_json_text(source_bytes: bytes) -> str:
    try:
        parsed = json.loads(source_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SourceParseError("JSON source is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SourceParseError("JSON source is malformed") from exc
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _decode_plain_text(source_bytes: bytes) -> str:
    try:
        return source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceParseError("Text source is not valid UTF-8") from exc


def parse_source_text(record: SourceDocumentRecord, source_bytes: bytes) -> str:
    content_type = (record.content_type or "").lower().strip()
    extension = ""
    if "." in record.original_filename:
        extension = "." + record.original_filename.lower().split(".")[-1]

    if content_type in JSON_CONTENT_TYPES or extension == ".json":
        return _decode_json_text(source_bytes)

    if content_type.startswith(TEXT_CONTENT_TYPE_PREFIX) or extension in TEXT_EXTENSIONS:
        return _decode_plain_text(source_bytes)

    raise UnsupportedSourceError(
        f"Unsupported source content type {record.content_type!r} and extension {extension!r}"
    )


def chunk_text(text: str, *, max_chars: int, overlap_chars: int) -> list[SourceChunkInput]:
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be >= 0")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    if not text:
        return []

    chunks: list[SourceChunkInput] = []
    index = 0
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk_text_value = text[start:end]
        chunks.append(
            SourceChunkInput(
                chunk_index=index,
                char_start=start,
                char_end=end,
                content_text=chunk_text_value,
                metadata={
                    "length": len(chunk_text_value),
                    "checksum_sha256": hashlib.sha256(
                        chunk_text_value.encode("utf-8")
                    ).hexdigest(),
                },
            )
        )
        if end >= len(text):
            break
        start = end - overlap_chars
        index += 1
    return chunks


def _is_retryable_error(exc: Exception) -> bool:
    return isinstance(exc, (FileNotFoundError, OSError))


class IngestionWorker:
    def __init__(
        self,
        *,
        repository: IngestionProcessingRepository,
        storage: ObjectStorage,
        max_chunk_chars: int,
        overlap_chars: int,
    ):
        self._repository = repository
        self._storage = storage
        self._max_chunk_chars = max_chunk_chars
        self._overlap_chars = overlap_chars

    def process_source(self, source_id: UUID) -> ProcessSourceResult:
        record = self._repository.get_source_document(source_id)
        if record is None:
            raise SourceNotFoundError(f"Source document {source_id} not found")

        try:
            source_bytes = self._storage.get_object(record.storage_key)
            source_text = parse_source_text(record, source_bytes)
            chunks = chunk_text(
                source_text,
                max_chars=self._max_chunk_chars,
                overlap_chars=self._overlap_chars,
            )
            chunk_count = self._repository.replace_source_chunks(record.id, chunks)
            return ProcessSourceResult(
                source_id=record.id,
                status="processed",
                chunk_count=chunk_count,
            )
        except Exception as exc:
            dlq_id = self._repository.enqueue_dlq(
                source_document_id=record.id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
                payload={
                    "storage_provider": record.storage_provider,
                    "storage_key": record.storage_key,
                    "content_type": record.content_type,
                    "original_filename": record.original_filename,
                },
                retryable=_is_retryable_error(exc),
            )
            return ProcessSourceResult(
                source_id=record.id,
                status="dlq",
                chunk_count=0,
                dlq_id=dlq_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
