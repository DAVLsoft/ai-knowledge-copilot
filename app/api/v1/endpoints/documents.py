import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, UploadFile, status

from app.api.deps import AIServiceDep, CurrentUser, SessionDep
from app.exception_handlers import documented_errors
from app.models import Document
from app.schemas.document import DocumentRead
from app.services.document import (
    create_document,
    get_documents,
    process_document,
)
from app.services.document import (
    delete_document as delete_document_service,
)
from app.services.document import (
    retry_document as retry_document_service,
)

router = APIRouter(tags=["Documents"])


@router.post(
    "/workspaces/{workspace_id}/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentRead,
)
async def upload_document(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    current_user: CurrentUser,
    ai_service: AIServiceDep,
    file: Annotated[UploadFile, File()],
) -> Document:
    document = await create_document(
        session=session,
        workspace_id=workspace_id,
        user_id=current_user.id,
        file=file,
    )
    background_tasks.add_task(process_document, document.id, ai_service)
    return document


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


@router.post(
    "/documents/{document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentRead,
    responses=documented_errors(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
async def retry_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    current_user: CurrentUser,
    ai_service: AIServiceDep,
) -> Document:
    document = await retry_document_service(session, document_id, current_user.id)
    background_tasks.add_task(process_document, document.id, ai_service)
    return document


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
