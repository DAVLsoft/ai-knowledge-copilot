from pathlib import Path, PurePosixPath


class FakeStorage:
    """In-memory StorageBackend."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    @staticmethod
    def _validate_key(key: str) -> str:
        parts: list[str] = []
        for part in PurePosixPath(key).parts:
            if part in {"/", ".."}:
                raise ValueError("Storage key escapes the storage root")
            if part != ".":
                parts.append(part)
        if not parts:
            raise ValueError("Storage key escapes the storage root")
        return "/".join(parts)

    async def save(self, key: str, data: bytes) -> None:
        self.files[self._validate_key(key)] = data

    async def read(self, key: str) -> bytes:
        normalized = self._validate_key(key)
        try:
            return self.files[normalized]
        except KeyError:
            raise FileNotFoundError(key) from None

    async def delete(self, key: str) -> None:
        self.files.pop(self._validate_key(key), None)

    async def exists(self, key: str) -> bool:
        return self._validate_key(key) in self.files

    def get_path(self, key: str) -> Path:
        return Path(self._validate_key(key))
