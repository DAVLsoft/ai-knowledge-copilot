import logging
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    DocumentNotFoundError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    WorkspaceNotFoundError,
)
from app.db.session import SessionLocal
from app.models.document import Document, DocumentStatus
from app.models.workspace import Workspace
from app.services.ai import SUPPORTED_EXTENSIONS, AIService
from app.services.ai.exceptions import DocumentParsingError
from app.storage.local import (
    FileTooLargeError as StorageFileTooLargeError,
)
from app.storage.local import (
    delete_file,
    save_file,
)

logger = logging.getLogger(__name__)


async def create_document(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
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

        try:
            file_path = await save_file(file)
        except StorageFileTooLargeError:
            raise FileTooLargeError("File size exceeds the maximum allowed size of 10 MB") from None

        document = Document(
            workspace_id=workspace.id,
            filename=file.filename or "unnamed",
            file_path=str(file_path),
            status=DocumentStatus.PENDING,
        )

        try:
            session.add(document)
            await session.commit()
            await session.refresh(document)
        except Exception:
            await session.rollback()
            delete_file(file_path)
            raise

        return document

    finally:
        await file.close()


async def process_document(document_id: uuid.UUID, ai_service: AIService) -> None:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None or document.status != DocumentStatus.PENDING:
            return
        await _ingest_document(session, document, ai_service)


async def _ingest_document(
    session: AsyncSession,
    document: Document,
    ai_service: AIService,
) -> None:
    document_id = document.id
    try:
        await ai_service.ingest_document(
            document_id=document_id,
            workspace_id=document.workspace_id,
            file_path=document.file_path,
            filename=document.filename,
        )
    except Exception as exc:
        logger.exception("Document ingestion failed for %s", document_id)
        document.status = DocumentStatus.FAILED
        document.failure_reason = "Document processing failed"
        if isinstance(exc, DocumentParsingError):
            document.failure_reason = str(exc)[:255]
    else:
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Failed to save ingestion result for document %s", document_id)
        raise


async def retry_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Document:
    try:
        document = await session.scalar(
            select(Document)
            .join(Workspace, Document.workspace_id == Workspace.id)
            .where(Document.id == document_id, Workspace.owner_id == user_id)
            .with_for_update(of=Document)
        )
        if document is None:
            raise DocumentNotFoundError("Document not found")
        if document.status != DocumentStatus.FAILED:
            raise ConflictError("Only failed documents can be retried")

        document.status = DocumentStatus.PENDING
        document.failure_reason = None
        await session.flush()
        await session.refresh(document)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return document


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

    file_path = Path(document.file_path)

    try:
        await session.delete(document)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    delete_file(file_path)
