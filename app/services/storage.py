import asyncio
from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    """File storage used for uploaded documents. Keys are relative, '/'-separated paths."""

    async def save(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str) -> bytes:
        """Raise FileNotFoundError if the key does not exist."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    def get_path(self, key: str) -> Path: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise ValueError("Storage key escapes the storage root")
        return path

    def get_path(self, key: str) -> Path:
        """Return the absolute path corresponding to a storage key."""
        return self._path(key)

    async def save(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)
