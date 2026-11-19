import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.workspace import Workspace
from app.services.ingestion import trigger_ingestion
from app.storage.local import delete_file, save_file


class WorkspaceNotFoundError(Exception):
    pass


class UnsupportedFileTypeError(Exception):
    pass


class DocumentNotFoundError(Exception):
    pass


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

        allowed_extensions = {".pdf", ".txt", ".docx"}
        file_extension = Path(file.filename or "").suffix.lower()

        if file_extension not in allowed_extensions:
            raise UnsupportedFileTypeError()

        file_path = await save_file(file)

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
        await trigger_ingestion(document.id)

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
        raise WorkspaceNotFoundError()

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
        raise DocumentNotFoundError()

    file_path = Path(document.file_path)

    try:
        await session.delete(document)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    delete_file(file_path)
