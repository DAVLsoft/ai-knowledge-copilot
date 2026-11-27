import asyncio
import uuid
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, NoReturn, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import get_ai_service
from app.core.errors import ConflictError
from app.main import app
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.services.ai import AIService
from app.services.document import retry_document
from tests.integration.factories import PASSWORD, create_user, create_workspace


@pytest.fixture
def worker_session(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # The worker gets its own session on the connection holding this test's outer transaction.
    factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr("app.services.document.SessionLocal", factory)


@pytest.mark.usefixtures("worker_session")
async def test_upload_document(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    ai_service_mock = AsyncMock()

    def override_ai_service() -> AIService:
        return cast(AIService, ai_service_mock)

    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        **app.dependency_overrides,
        get_ai_service: override_ai_service,
    }
    monkeypatch.setattr(app, "dependency_overrides", overrides)

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("report.txt", BytesIO(b"Test document"), "text/plain")},
    )

    assert response.status_code == 201

    body = response.json()

    result = await db_session.execute(select(Document).where(Document.id == uuid.UUID(body["id"])))
    document = result.scalar_one()

    file_path = Path(document.file_path)

    assert await asyncio.to_thread(file_path.exists)
    content = await asyncio.to_thread(file_path.read_bytes)
    assert content == b"Test document"
    assert body["workspace_id"] == str(workspace.id)
    assert body["filename"] == "report.txt"
    assert body["status"] == DocumentStatus.PENDING.value
    assert body["failure_reason"] is None
    assert document.status == DocumentStatus.PROCESSED
    assert document.failure_reason is None
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body
    assert "file_path" not in body

    ai_service_mock.ingest_document.assert_awaited_once_with(
        document_id=uuid.UUID(body["id"]),
        workspace_id=workspace.id,
        file_path=document.file_path,
        filename=document.filename,
    )


@pytest.mark.usefixtures("worker_session")
async def test_upload_document_ingestion_error_and_retry(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    ai_service_mock = AsyncMock()
    ai_service_mock.ingest_document.side_effect = RuntimeError("Ingestion failed")

    def override_ai_service() -> AIService:
        return cast(AIService, ai_service_mock)

    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        **app.dependency_overrides,
        get_ai_service: override_ai_service,
    }
    monkeypatch.setattr(app, "dependency_overrides", overrides)

    headers = {"Authorization": f"Bearer {access_token}"}
    documents_url = f"/api/v1/workspaces/{workspace.id}/documents"
    response = await client.post(
        documents_url,
        headers=headers,
        files={"file": ("report.txt", b"Test document", "text/plain")},
    )
    assert response.status_code == 201
    assert response.json()["status"] == DocumentStatus.PENDING.value

    result = await db_session.execute(select(Document).where(Document.workspace_id == workspace.id))
    document = result.scalar_one()
    assert document.status == DocumentStatus.FAILED
    assert document.failure_reason == "Document processing failed"

    file_path = Path(document.file_path)

    assert await asyncio.to_thread(file_path.exists)
    content = await asyncio.to_thread(file_path.read_bytes)
    assert content == b"Test document"
    ai_service_mock.ingest_document.assert_awaited_once_with(
        document_id=document.id,
        workspace_id=workspace.id,
        file_path=document.file_path,
        filename=document.filename,
    )

    listed = await client.get(documents_url, headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["failure_reason"] == "Document processing failed"

    ai_service_mock.ingest_document.side_effect = None
    retry_url = f"/api/v1/documents/{document.id}/retry"
    retry = await client.post(retry_url, headers=headers)
    assert retry.status_code == 202
    assert retry.json()["id"] == str(document.id)
    assert retry.json()["status"] == DocumentStatus.PENDING.value
    assert retry.json()["failure_reason"] is None

    await db_session.refresh(document)
    listed = await client.get(documents_url, headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == DocumentStatus.PROCESSED.value
    assert listed.json()[0]["failure_reason"] is None
    assert ai_service_mock.ingest_document.await_count == 2


@pytest.mark.parametrize(
    ("document_status", "owned", "expected_status"),
    [
        (None, True, 404),
        (DocumentStatus.FAILED, False, 404),
        (DocumentStatus.PENDING, True, 409),
        (DocumentStatus.PROCESSED, True, 409),
    ],
)
async def test_retry_document_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    document_status: DocumentStatus | None,
    owned: bool,
    expected_status: int,
) -> None:
    user = await create_user(db_session)
    document_id = uuid.uuid4()
    if document_status is not None:
        owner = (
            user
            if owned
            else await create_user(db_session, email="owner@example.com", username="owner")
        )
        workspace = await create_workspace(db_session, owner_id=owner.id)
        db_session.add(
            Document(
                id=document_id,
                workspace_id=workspace.id,
                filename="report.txt",
                file_path="report.txt",
                status=document_status,
            )
        )
        await db_session.commit()

    worker = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.documents.process_document", worker)
    login = await client.post("/api/v1/login", data={"username": user.email, "password": PASSWORD})
    response = await client.post(
        f"/api/v1/documents/{document_id}/retry",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == expected_status
    worker.assert_not_awaited()


async def test_concurrent_retries_only_accept_one_request(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:12]
    async with factory() as session:
        user = await create_user(
            session, email=f"retry-{suffix}@example.com", username=f"retry_{suffix}"
        )
        user_id = user.id

    try:
        async with factory() as session:
            workspace = await create_workspace(session, owner_id=user_id)
            document = Document(
                workspace_id=workspace.id,
                filename="report.txt",
                file_path="report.txt",
                status=DocumentStatus.FAILED,
                failure_reason="Previous failure",
            )
            session.add(document)
            await session.commit()
            document_id = document.id

        async def attempt_retry() -> DocumentStatus:
            async with factory() as session:
                result = await retry_document(session, document_id, user_id)
                return result.status

        outcomes = await asyncio.gather(attempt_retry(), attempt_retry(), return_exceptions=True)
        assert outcomes.count(DocumentStatus.PENDING) == 1
        assert sum(isinstance(outcome, ConflictError) for outcome in outcomes) == 1
    finally:
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == user_id))


async def test_upload_document_refresh_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rollback_mock = AsyncMock(wraps=db_session.rollback)
    monkeypatch.setattr(db_session, "rollback", rollback_mock)
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    refresh_mock = AsyncMock(side_effect=RuntimeError("Database refresh failed"))
    monkeypatch.setattr(db_session, "refresh", refresh_mock)

    with pytest.raises(RuntimeError, match="Database refresh failed"):
        await client.post(
            f"/api/v1/workspaces/{workspace.id}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"file": ("test.txt", b"test content", "text/plain")},
        )

    files = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert files == []
    rollback_mock.assert_awaited_once()
    refresh_mock.assert_awaited_once()


async def test_upload_document_unsupported_format(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("image.png", BytesIO(b"Fake image"), "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unsupported file format. Allowed formats: DOCX, MD, PDF, TXT"
    )


async def test_upload_document_workspace_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    response = await client.post(
        f"/api/v1/workspaces/{uuid.uuid4()}/documents",
        headers=headers,
        files={"file": ("report.txt", BytesIO(b"Test document"), "text/plain")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"


async def test_upload_document_file_too_large(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    large_content = b"x" * (10 * 1024 * 1024 + 1)

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("large.txt", BytesIO(large_content), "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == ("File size exceeds the maximum allowed size of 10 MB")

    files = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert files == []


@pytest.mark.usefixtures("worker_session")
async def test_list_documents(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    upload_response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("report.txt", BytesIO(b"Test document"), "text/plain")},
    )
    assert upload_response.status_code == 201

    response = await client.get(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()

    assert len(body) == 1
    assert body[0]["filename"] == "report.txt"
    assert body[0]["workspace_id"] == str(workspace.id)
    assert body[0]["status"] == DocumentStatus.PROCESSED.value
    assert "file_path" not in body[0]


async def test_list_documents_database_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    async def failing_execute(*args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("Database query failed")

    monkeypatch.setattr(db_session, "execute", failing_execute)

    with pytest.raises(RuntimeError, match="Database query failed"):
        await client.get(
            f"/api/v1/workspaces/{workspace.id}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
        )


@pytest.mark.usefixtures("worker_session")
async def test_delete_document(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    upload_response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("report.txt", BytesIO(b"Test document"), "text/plain")},
    )
    assert upload_response.status_code == 201

    document_id = upload_response.json()["id"]

    result = await db_session.execute(select(Document).where(Document.id == uuid.UUID(document_id)))
    document = result.scalar_one()
    file_path = Path(document.file_path)

    assert await asyncio.to_thread(file_path.exists)

    response = await client.delete(
        f"/api/v1/documents/{document_id}",
        headers=headers,
    )

    assert response.status_code == 204
    assert not await asyncio.to_thread(file_path.exists)

    result = await db_session.execute(select(Document).where(Document.id == uuid.UUID(document_id)))
    assert result.scalar_one_or_none() is None


async def test_delete_document_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    document_id = uuid.uuid4()

    response = await client.delete(
        f"/api/v1/documents/{document_id}",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


@pytest.mark.usefixtures("worker_session")
async def test_delete_document_from_another_users_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    owner = await create_user(db_session)
    other_user = await create_user(
        db_session,
        email="other@example.com",
        username="otheruser",
    )
    workspace = await create_workspace(
        db_session,
        owner_id=owner.id,
    )

    login = await client.post(
        "/api/v1/login",
        data={"username": owner.email, "password": PASSWORD},
    )
    token = login.json()["access_token"]

    upload = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.txt", b"test content", "text/plain")},
    )
    document_id = upload.json()["id"]

    other_login = await client.post(
        "/api/v1/login",
        data={"username": other_user.email, "password": PASSWORD},
    )
    other_token = other_login.json()["access_token"]

    response = await client.delete(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


async def test_upload_document_commit_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rollback_mock = AsyncMock(wraps=db_session.rollback)
    monkeypatch.setattr(db_session, "rollback", rollback_mock)
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    async def failing_commit() -> NoReturn:
        raise RuntimeError("Database commit failed")

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(RuntimeError, match="Database commit failed"):
        await client.post(
            f"/api/v1/workspaces/{workspace.id}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"file": ("test.txt", b"test content", "text/plain")},
        )

    files = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert files == []
    rollback_mock.assert_awaited_once()


@pytest.mark.usefixtures("worker_session")
async def test_delete_document_commit_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    upload_response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers=headers,
        files={"file": ("report.txt", BytesIO(b"Test document"), "text/plain")},
    )
    assert upload_response.status_code == 201

    document_id = upload_response.json()["id"]
    files = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    file_path = files[0]

    rollback_mock = AsyncMock(wraps=db_session.rollback)
    monkeypatch.setattr(db_session, "rollback", rollback_mock)

    async def failing_commit() -> NoReturn:
        raise RuntimeError("Database commit failed")

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(RuntimeError, match="Database commit failed"):
        await client.delete(
            f"/api/v1/documents/{document_id}",
            headers=headers,
        )

    assert file_path.exists()
    rollback_mock.assert_awaited_once()


async def test_upload_document_storage_error(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.storage.local.settings.uploads_dir", tmp_path)

    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    async def failing_save_file(file: UploadFile) -> NoReturn:
        raise OSError("Disk write failed")

    monkeypatch.setattr(
        "app.services.document.save_file",
        failing_save_file,
    )

    with pytest.raises(OSError, match="Disk write failed"):
        await client.post(
            f"/api/v1/workspaces/{workspace.id}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"file": ("report.txt", b"Test document", "text/plain")},
        )

    files = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert files == []


async def test_list_documents_empty(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    response = await client.get(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_documents_from_another_users_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await create_user(db_session)
    other_user = await create_user(
        db_session,
        email="other@example.com",
        username="otheruser",
    )
    workspace = await create_workspace(db_session, owner_id=owner.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": other_user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    response = await client.get(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"


async def test_upload_document_to_another_users_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await create_user(db_session)
    other_user = await create_user(
        db_session,
        email="other@example.com",
        username="otheruser",
    )
    workspace = await create_workspace(db_session, owner_id=owner.id)

    login = await client.post(
        "/api/v1/login",
        data={"username": other_user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/documents",
        headers={"Authorization": f"Bearer {access_token}"},
        files={"file": ("report.txt", b"Test document", "text/plain")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"


async def test_delete_document_when_file_missing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = await create_user(db_session)
    workspace = await create_workspace(db_session, owner_id=user.id)

    document = Document(
        workspace_id=workspace.id,
        filename="missing.txt",
        file_path=str(tmp_path / "missing.txt"),
        status=DocumentStatus.PENDING,
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)

    login = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )
    access_token = login.json()["access_token"]

    response = await client.delete(
        f"/api/v1/documents/{document.id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204

    result = await db_session.execute(select(Document).where(Document.id == document.id))
    assert result.scalar_one_or_none() is None
