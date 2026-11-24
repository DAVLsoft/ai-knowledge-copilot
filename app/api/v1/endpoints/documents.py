import uuid
from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import CurrentUser, SessionDep
from app.models import Document
from app.schemas.document import DocumentRead
from app.services.document import (
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
    response_model=DocumentRead,
)
async def upload_document(
    workspace_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> Document:
    return await create_document(
        session=session,
        workspace_id=workspace_id,
        user_id=current_user.id,
        file=file,
    )


@router.get(
    "/workspaces/{workspace_id}/documents",
    status_code=status.HTTP_200_OK,
    response_model=list[DocumentRead],
)
async def list_documents(
    workspace_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> list[Document]:
    return await get_documents(
        session=session,
        workspace_id=workspace_id,
        user_id=current_user.id,
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    await delete_document_service(
        session=session,
        document_id=document_id,
        user_id=current_user.id,
    )
