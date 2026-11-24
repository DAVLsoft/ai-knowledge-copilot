from collections.abc import Sequence
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, RAGDep, SessionDep
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.exception_handlers import documented_errors
from app.models.workspace import Workspace
from app.schemas.workspace import ChatRequest, ChatResponse, WorkspaceCreate, WorkspaceRead
from app.services import workspace as workspace_service
from app.services.rag import UnconfiguredRAGService

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    responses=documented_errors(HTTPStatus.UNAUTHORIZED, HTTPStatus.UNPROCESSABLE_ENTITY),
)


@router.get("", response_model=list[WorkspaceRead])
async def workspace_list(
    session: SessionDep,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Sequence[Workspace]:
    """List workspaces owned by the authenticated user."""
    return await workspace_service.list_workspaces(
        session=session,
        owner_id=user.id,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/{workspace_id}/chat",
    responses=documented_errors(HTTPStatus.NOT_FOUND, HTTPStatus.SERVICE_UNAVAILABLE),
)
async def query_workspace(
    workspace_id: UUID, data: ChatRequest, user: CurrentUser, session: SessionDep, rag: RAGDep
) -> ChatResponse:
    """Ask a question in an owned workspace and return the answer and source document IDs."""
    workspace = await workspace_service.get_owned_workspace(
        session=session,
        workspace_id=workspace_id,
        owner_id=user.id,
    )
    if workspace is None:
        raise NotFoundError("Workspace not found")
    if isinstance(rag, UnconfiguredRAGService):
        raise ServiceUnavailableError("Chat service is unavailable")

    response = await rag.answer(workspace.id, data.question)
    return ChatResponse(
        answer=response.answer,
        source_document_ids=response.source_document_ids,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkspaceRead)
async def workspace_create(
    session: SessionDep, user: CurrentUser, data: WorkspaceCreate
) -> Workspace:
    """Create a workspace owned by the authenticated user."""
    return await workspace_service.create_workspace(
        session=session,
        owner_id=user.id,
        name=data.name,
    )
