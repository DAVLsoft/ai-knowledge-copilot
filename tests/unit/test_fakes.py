from pathlib import Path

import pytest

from app.services.storage import LocalStorage, StorageBackend
from tests.fakes import FakeStorage


@pytest.fixture(params=["fake", "local"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> StorageBackend:
    if request.param == "fake":
        return FakeStorage()
    return LocalStorage(tmp_path)


async def test_storage_roundtrip(storage: StorageBackend) -> None:
    await storage.save("a/b.txt", b"hi")
    assert await storage.exists("a/b.txt")
    assert await storage.read("a/b.txt") == b"hi"
    await storage.delete("a/b.txt")
    assert not await storage.exists("a/b.txt")
    with pytest.raises(FileNotFoundError):
        await storage.read("a/b.txt")


@pytest.mark.parametrize("bad_key", ["../evil", "/evil", "", "."])
async def test_storage_rejects_invalid_keys(storage: StorageBackend, bad_key: str) -> None:
    with pytest.raises(ValueError, match="escapes"):
        await storage.save(bad_key, b"x")
