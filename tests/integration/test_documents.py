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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_ai_service
from app.main import app
from app.models.document import Document, DocumentStatus
from app.services.ai import AIService
from tests.integration.factories import PASSWORD, create_user, create_workspace


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
    assert body["status"] == DocumentStatus.PROCESSED.value
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


async def test_upload_document_ingestion_error(
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

    with pytest.raises(RuntimeError, match="Ingestion failed"):
        await client.post(
            f"/api/v1/workspaces/{workspace.id}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"file": ("report.txt", b"Test document", "text/plain")},
        )

    result = await db_session.execute(select(Document).where(Document.workspace_id == workspace.id))
    document = result.scalar_one()
    assert document.status == DocumentStatus.FAILED

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
