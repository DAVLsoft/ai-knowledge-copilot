import logging
import uuid
from contextlib import suppress
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DocumentNotFoundError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    WorkspaceNotFoundError,
)
from app.models.document import Document, DocumentStatus
from app.models.workspace import Workspace
from app.services.ai import AIService
from app.services.ai.stub import SUPPORTED_EXTENSIONS
from app.services.storage import StorageBackend

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    content = bytearray()

    while chunk := await file.read(UPLOAD_CHUNK_SIZE):
        if len(content) + len(chunk) > MAX_FILE_SIZE:
            raise FileTooLargeError("File size exceeds the maximum allowed size of 10 MB")

        content.extend(chunk)

    return bytes(content)


async def create_document(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    ai_service: AIService,
    storage: StorageBackend,
    file: UploadFile,
) -> Document:

    try:
        workspace = await get_workspace(
            session=session,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        file_extension = Path(file.filename or "").suffix.lower()

        allowed_formats = ", ".join(
            extension.lstrip(".").upper() for extension in sorted(SUPPORTED_EXTENSIONS)
        )

        if file_extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError(
                f"Unsupported file format. Allowed formats: {allowed_formats}"
            )

        content = await _read_upload(file)

        storage_key = f"documents/{uuid.uuid4()}{file_extension}"

        try:
            await storage.save(storage_key, content)
        except Exception:
            try:
                await storage.delete(storage_key)
            except Exception:
                logger.exception(
                    "Failed to clean up file %s after storage error",
                    storage_key,
                )
            raise

        document = Document(
            workspace_id=workspace.id,
            filename=file.filename or "unnamed",
            file_path=storage_key,
            status=DocumentStatus.PENDING,
        )

        try:
            session.add(document)
            await session.flush()
            await session.refresh(document)
            await session.commit()
        except Exception:
            await session.rollback()

            try:
                await storage.delete(storage_key)
            except Exception:
                logger.exception(
                    "Failed to clean up file %s after database error",
                    storage_key,
                )

            raise

        try:
            await ai_service.ingest_document(
                document_id=document.id,
                workspace_id=document.workspace_id,
                file_path=storage.get_path(storage_key),
                filename=document.filename,
            )
        except Exception:
            document.status = DocumentStatus.FAILED

            try:
                await session.commit()
                await session.refresh(document)
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to update document %s status to FAILED",
                    document.id,
                )

            raise
        else:
            document.status = DocumentStatus.PROCESSED
            await session.commit()
            await session.refresh(document)

        return document

    finally:
        await file.close()


async def get_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Workspace:
    result = await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == user_id,
        )
    )

    workspace = result.scalar_one_or_none()

    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found")

    return workspace


async def get_documents(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[Document]:
    await get_workspace(session, workspace_id, user_id)

    result = await session.execute(
        select(Document)
        .where(Document.workspace_id == workspace_id)
        .order_by(Document.created_at.desc())
    )

    return list(result.scalars().all())


async def delete_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    storage: StorageBackend,
) -> None:
    result = await session.execute(
        select(Document)
        .join(Workspace, Document.workspace_id == Workspace.id)
        .where(
            Document.id == document_id,
            Workspace.owner_id == user_id,
        )
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise DocumentNotFoundError("Document not found")

    storage_key = document.file_path

    file_content: bytes | None = None

    if await storage.exists(storage_key):
        with suppress(FileNotFoundError):
            file_content = await storage.read(storage_key)

    try:
        await storage.delete(storage_key)

        await session.delete(document)
        await session.commit()

    except Exception:
        await session.rollback()

        if file_content is not None:
            try:
                await storage.save(storage_key, file_content)
            except Exception:
                logger.exception(
                    "Failed to restore file %s after document deletion error",
                    storage_key,
                )

        raise
