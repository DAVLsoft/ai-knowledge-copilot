import uuid
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.document as document_service
from app.core.errors import (
    DocumentNotFoundError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    WorkspaceNotFoundError,
)
from app.models.document import Document, DocumentStatus
from app.services.ai import AIService
from app.services.storage import LocalStorage, StorageBackend


def make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


def make_file(filename: str = "report.txt") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=BytesIO(b"Test document"),
    )


@pytest.fixture
def workspace() -> MagicMock:
    return MagicMock(id=uuid.uuid4())


@pytest.fixture
def session() -> MagicMock:
    return make_session()


@pytest.fixture
def storage(tmp_path: Path) -> StorageBackend:
    return LocalStorage(tmp_path)


async def test_create_document_success(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock()

    document = await document_service.create_document(
        session=cast(AsyncSession, session),
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
        ai_service=cast(AIService, ai_service),
        storage=storage,
        file=file,
    )

    assert document.status == DocumentStatus.PROCESSED
    assert document.file_path.startswith("documents/")
    assert document.file_path.endswith(".txt")

    stored_path = storage.get_path(document.file_path)

    assert stored_path.is_file()
    assert stored_path.read_bytes() == b"Test document"

    session.add.assert_called_once_with(document)
    assert session.commit.await_count == 2
    assert session.refresh.await_count == 2

    ai_service.ingest_document.assert_awaited_once()

    ai_call = ai_service.ingest_document.await_args.kwargs
    assert ai_call["file_path"] == stored_path
    assert ai_call["filename"] == "report.txt"

    assert file.file.closed


async def test_create_document_ingestion_error(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock(side_effect=RuntimeError("Ingestion failed"))

    with pytest.raises(RuntimeError, match="Ingestion failed"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, ai_service),
            storage=storage,
            file=file,
        )

    document = session.add.call_args.args[0]

    assert isinstance(document, Document)
    assert document.status == DocumentStatus.FAILED
    assert document.file_path.startswith("documents/")

    stored_path = storage.get_path(document.file_path)

    assert await storage.exists(document.file_path)
    assert await storage.read(document.file_path) == b"Test document"

    assert session.commit.await_count == 2
    assert session.refresh.await_count == 2
    session.rollback.assert_not_awaited()

    ai_service.ingest_document.assert_awaited_once()
    assert ai_service.ingest_document.await_args.kwargs["file_path"] == stored_path

    assert file.file.closed


async def test_create_document_unsupported_file_type(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file("report.exe")

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    save_mock = AsyncMock()
    monkeypatch.setattr(storage, "save", save_mock)

    with pytest.raises(UnsupportedFileTypeError):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, MagicMock()),
            storage=storage,
            file=file,
        )

    save_mock.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    assert file.file.closed


async def test_create_document_commit_error_deletes_file(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    session.commit.side_effect = RuntimeError("Database error")

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, MagicMock()),
            storage=storage,
            file=file,
        )

    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()

    documents_dir = storage.get_path("documents")
    assert not documents_dir.exists() or not any(documents_dir.iterdir())

    assert file.file.closed


async def test_create_document_file_too_large(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    monkeypatch.setattr(
        document_service,
        "_read_upload",
        AsyncMock(
            side_effect=FileTooLargeError("File size exceeds the maximum allowed size of 10 MB")
        ),
    )

    with pytest.raises(FileTooLargeError):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, MagicMock()),
            storage=storage,
            file=file,
        )

    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()

    documents_dir = storage.get_path("documents")
    assert not documents_dir.exists() or not any(documents_dir.iterdir())

    assert file.file.closed


async def test_create_document_failed_status_commit_error(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock(side_effect=RuntimeError("Ingestion failed"))

    session.commit.side_effect = [
        None,
        RuntimeError("Status update failed"),
    ]

    with pytest.raises(RuntimeError, match="Ingestion failed"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, ai_service),
            storage=storage,
            file=file,
        )

    document = session.add.call_args.args[0]

    assert document.status == DocumentStatus.FAILED
    assert document.file_path.startswith("documents/")

    assert session.flush.await_count == 1
    assert session.commit.await_count == 2
    session.rollback.assert_awaited_once()

    assert await storage.exists(document.file_path)
    assert await storage.read(document.file_path) == b"Test document"

    ai_service.ingest_document.assert_awaited_once()
    assert file.file.closed


async def test_get_workspace_success(
    workspace: MagicMock,
) -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = workspace
    session.execute = AsyncMock(return_value=result)

    found = await document_service.get_workspace(
        session=cast(AsyncSession, session),
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
    )

    assert found is workspace
    session.execute.assert_awaited_once()


async def test_get_workspace_not_found() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    with pytest.raises(WorkspaceNotFoundError):
        await document_service.get_workspace(
            session=cast(AsyncSession, session),
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )

    session.execute.assert_awaited_once()


async def test_get_documents_success(
    workspace: MagicMock,
) -> None:
    session = MagicMock()

    workspace_result = MagicMock()
    workspace_result.scalar_one_or_none.return_value = workspace

    documents = [MagicMock(spec=Document), MagicMock(spec=Document)]

    documents_result = MagicMock()
    documents_result.scalars.return_value.all.return_value = documents

    session.execute = AsyncMock(side_effect=[workspace_result, documents_result])

    found = await document_service.get_documents(
        session=cast(AsyncSession, session),
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
    )

    assert found == documents
    assert session.execute.await_count == 2


async def test_delete_document_removes_database_record_and_file(
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    storage_key = "documents/test-document.txt"
    await storage.save(storage_key, b"Test document")

    document = MagicMock(spec=Document)
    document.id = uuid.uuid4()
    document.file_path = storage_key

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result

    await document_service.delete_document(
        session=cast(AsyncSession, session),
        document_id=document.id,
        user_id=uuid.uuid4(),
        storage=storage,
    )

    assert not await storage.exists(storage_key)
    session.delete.assert_awaited_once_with(document)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_delete_document_not_found(
    storage: StorageBackend,
) -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    with pytest.raises(DocumentNotFoundError):
        await document_service.delete_document(
            session=cast(AsyncSession, session),
            document_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            storage=storage,
        )

    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_delete_document_commit_error(
    storage: StorageBackend,
) -> None:
    session = MagicMock()

    storage_key = "documents/report.txt"
    await storage.save(storage_key, b"Test document")

    document = Document(
        workspace_id=uuid.uuid4(),
        filename="report.txt",
        file_path=storage_key,
        status=DocumentStatus.PROCESSED,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute = AsyncMock(return_value=result)
    session.delete = AsyncMock()
    session.commit = AsyncMock(side_effect=RuntimeError("Database error"))
    session.rollback = AsyncMock()

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.delete_document(
            session=cast(AsyncSession, session),
            document_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            storage=storage,
        )

    session.delete.assert_awaited_once_with(document)
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()

    assert await storage.exists(storage_key)
    assert await storage.read(storage_key) == b"Test document"


async def test_delete_document_when_file_is_missing(
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    storage_key = "documents/missing.txt"

    document = MagicMock(spec=Document)
    document.id = uuid.uuid4()
    document.file_path = storage_key

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result

    assert not await storage.exists(storage_key)

    await document_service.delete_document(
        session=cast(AsyncSession, session),
        document_id=document.id,
        user_id=uuid.uuid4(),
        storage=storage,
    )

    assert not await storage.exists(storage_key)
    session.delete.assert_awaited_once_with(document)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_delete_document_restores_file_on_database_error(
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    storage_key = "documents/restore.txt"
    original_content = b"Important document"

    await storage.save(storage_key, original_content)

    document = MagicMock(spec=Document)
    document.id = uuid.uuid4()
    document.file_path = storage_key

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result

    session.commit.side_effect = RuntimeError("Database error")

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.delete_document(
            session=cast(AsyncSession, session),
            document_id=document.id,
            user_id=uuid.uuid4(),
            storage=storage,
        )

    session.delete.assert_awaited_once_with(document)
    session.rollback.assert_awaited_once()

    assert await storage.exists(storage_key)
    assert await storage.read(storage_key) == original_content


async def test_delete_document_storage_error(
    session: MagicMock,
    storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_key = "documents/delete-error.txt"
    await storage.save(storage_key, b"Test document")

    document = MagicMock(spec=Document)
    document.id = uuid.uuid4()
    document.file_path = storage_key

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result

    delete_mock = AsyncMock(side_effect=RuntimeError("Storage delete failed"))
    monkeypatch.setattr(storage, "delete", delete_mock)

    with pytest.raises(RuntimeError, match="Storage delete failed"):
        await document_service.delete_document(
            session=cast(AsyncSession, session),
            document_id=document.id,
            user_id=uuid.uuid4(),
            storage=storage,
        )

    delete_mock.assert_awaited_once_with(storage_key)
    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()

    assert await storage.exists(storage_key)


async def test_create_document_database_error_cleans_up_file(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    session.commit.side_effect = RuntimeError("Database error")

    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock()

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, ai_service),
            storage=storage,
            file=file,
        )

    document = session.add.call_args.args[0]
    storage_key = document.file_path

    assert storage_key.startswith("documents/")
    assert not await storage.exists(storage_key)

    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()

    ai_service.ingest_document.assert_not_awaited()
    assert file.file.closed


async def test_create_document_storage_error_cleans_up_file(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    storage: StorageBackend,
) -> None:
    file = make_file()
    saved_keys: list[str] = []

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    original_save = storage.save

    async def failing_save(key: str, data: bytes) -> None:
        saved_keys.append(key)
        await original_save(key, data)
        raise RuntimeError("Storage write failed")

    monkeypatch.setattr(storage, "save", failing_save)

    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock()

    with pytest.raises(RuntimeError, match="Storage write failed"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            ai_service=cast(AIService, ai_service),
            storage=storage,
            file=file,
        )

    assert len(saved_keys) == 1
    assert not await storage.exists(saved_keys[0])

    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    ai_service.ingest_document.assert_not_awaited()

    assert file.file.closed
