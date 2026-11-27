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
from app.services.ai.exceptions import DocumentParsingError
from app.storage.local import FileTooLargeError as StorageFileTooLargeError


def make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
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


async def test_create_document_success(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
    tmp_path: Path,
) -> None:
    file = make_file()
    file_path = tmp_path / "report.txt"

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        document_service,
        "save_file",
        AsyncMock(return_value=file_path),
    )

    document = await document_service.create_document(
        session=cast(AsyncSession, session),
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
        file=file,
    )

    assert document.status == DocumentStatus.PENDING
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(document)
    assert file.file.closed


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DocumentParsingError("PDF is password-protected"), "PDF is password-protected"),
        (RuntimeError("Internal details"), "Document processing failed"),
        (None, None),
    ],
)
async def test_process_document(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    error: Exception | None,
    reason: str | None,
) -> None:
    document = Document(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        filename="report.pdf",
        file_path="uploads/report.pdf",
        status=DocumentStatus.PENDING,
    )
    session.get = AsyncMock(return_value=document)
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(document_service, "SessionLocal", MagicMock(return_value=session_context))
    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock(side_effect=error)

    await document_service.process_document(document.id, cast(AIService, ai_service))

    session.get.assert_awaited_once_with(Document, document.id)
    ai_service.ingest_document.assert_awaited_once_with(
        document_id=document.id,
        workspace_id=document.workspace_id,
        file_path=document.file_path,
        filename=document.filename,
    )
    assert document.status == (DocumentStatus.FAILED if error else DocumentStatus.PROCESSED)
    assert document.failure_reason == reason
    session.commit.assert_awaited_once()
    session_context.__aexit__.assert_awaited_once()


async def test_create_document_unsupported_file_type(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
) -> None:
    file = make_file("report.exe")

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )

    save_mock = AsyncMock()
    monkeypatch.setattr(document_service, "save_file", save_mock)

    with pytest.raises(UnsupportedFileTypeError):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
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
    tmp_path: Path,
) -> None:
    file = make_file()
    file_path = tmp_path / "report.txt"

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        document_service,
        "save_file",
        AsyncMock(return_value=file_path),
    )

    delete_mock = MagicMock()
    monkeypatch.setattr(document_service, "delete_file", delete_mock)

    session.commit.side_effect = RuntimeError("Database error")

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            file=file,
        )

    session.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with(file_path)
    assert file.file.closed


async def test_create_document_file_too_large(
    monkeypatch: pytest.MonkeyPatch,
    workspace: MagicMock,
    session: MagicMock,
) -> None:
    file = make_file()

    monkeypatch.setattr(
        document_service,
        "get_workspace",
        AsyncMock(return_value=workspace),
    )
    monkeypatch.setattr(
        document_service,
        "save_file",
        AsyncMock(side_effect=StorageFileTooLargeError()),
    )

    with pytest.raises(FileTooLargeError):
        await document_service.create_document(
            session=cast(AsyncSession, session),
            workspace_id=workspace.id,
            user_id=uuid.uuid4(),
            file=file,
        )

    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    assert file.file.closed


async def test_ingest_document_failed_status_commit_error(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
) -> None:
    document = Document(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        filename="report.txt",
        file_path="uploads/report.txt",
        status=DocumentStatus.PENDING,
    )
    ai_service = MagicMock()
    ai_service.ingest_document = AsyncMock(side_effect=RuntimeError("Ingestion failed"))
    session.commit.side_effect = RuntimeError("Status update failed")
    session.get = AsyncMock(return_value=document)
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(document_service, "SessionLocal", MagicMock(return_value=session_context))

    with pytest.raises(RuntimeError, match="Status update failed"):
        await document_service.process_document(document.id, cast(AIService, ai_service))

    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()


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


async def test_delete_document_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.execute = AsyncMock()

    document = Document(
        workspace_id=uuid.uuid4(),
        filename="report.txt",
        file_path="uploads/report.txt",
        status=DocumentStatus.PROCESSED,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    delete_file_mock = MagicMock()
    monkeypatch.setattr(document_service, "delete_file", delete_file_mock)

    await document_service.delete_document(
        session=cast(AsyncSession, session),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    session.delete.assert_awaited_once_with(document)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    delete_file_mock.assert_called_once_with(Path(document.file_path))


async def test_delete_document_not_found() -> None:
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
        )

    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_delete_document_commit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()

    document = Document(
        workspace_id=uuid.uuid4(),
        filename="report.txt",
        file_path="uploads/report.txt",
        status=DocumentStatus.PROCESSED,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute = AsyncMock(return_value=result)
    session.delete = AsyncMock()
    session.commit = AsyncMock(side_effect=RuntimeError("Database error"))
    session.rollback = AsyncMock()

    delete_file_mock = MagicMock()
    monkeypatch.setattr(document_service, "delete_file", delete_file_mock)

    with pytest.raises(RuntimeError, match="Database error"):
        await document_service.delete_document(
            session=cast(AsyncSession, session),
            document_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )

    session.delete.assert_awaited_once_with(document)
    session.rollback.assert_awaited_once()
    delete_file_mock.assert_not_called()
