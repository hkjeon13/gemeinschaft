"""Object storage adapter(s) for ingestion artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    provider: str

    def put_object(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Persist raw bytes for a given storage key."""

    def get_object(self, key: str) -> bytes:
        """Read raw bytes by storage key."""

    def delete_object(self, key: str) -> None:
        """Delete a stored object by key."""


class LocalObjectStorage:
    """Filesystem-backed object storage adapter for local development."""

    provider = "local_fs"

    def __init__(self, root: Path):
        self._root = root

    def put_object(self, key: str, data: bytes, content_type: str | None = None) -> None:
        del content_type  # Content type isn't needed for filesystem storage.
        destination = self._root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def get_object(self, key: str) -> bytes:
        destination = self._root / key
        return destination.read_bytes()

    def delete_object(self, key: str) -> None:
        destination = self._root / key
        if destination.exists():
            destination.unlink()
