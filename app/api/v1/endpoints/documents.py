import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, SessionDep
from app.services.document import (
    DocumentNotFoundError,
    UnsupportedFileTypeError,
    WorkspaceNotFoundError,
    create_document,
    get_documents,
)
from app.services.document import (
    delete_document as delete_document_service,
)

router = APIRouter(tags=["Documents"])


@router.post(
    "/workspaces/{workspace_id}/documents",
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
):
    try:
        document = await create_document(
            session=session,
            workspace_id=workspace_id,
            user_id=current_user.id,
            file=file,
        )
    except WorkspaceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        ) from None
    except UnsupportedFileTypeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Allowed formats: PDF, TXT, DOCX",
        ) from None

    return document


@router.get(
    "/workspaces/{workspace_id}/documents",
    status_code=status.HTTP_200_OK,
)
async def list_documents(
    workspace_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    try:
        documents = await get_documents(
            session=session,
            workspace_id=workspace_id,
            user_id=current_user.id,
        )
    except WorkspaceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        ) from None

    return documents


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    try:
        await delete_document_service(
            session=session,
            document_id=document_id,
            user_id=current_user.id,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        ) from None
