from collections.abc import Sequence
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import AIServiceDep, CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.exception_handlers import documented_errors
from app.models.chat_message import WorkspaceChatMessage
from app.models.workspace import Workspace
from app.schemas.workspace import (
    ChatRequest,
    WorkspaceChatMessageRead,
    WorkspaceCreate,
    WorkspaceRead,
)
from app.services import chat_history as chat_history_service
from app.services import workspace as workspace_service
from app.services.ai import ChatMessage, ChatRole, WorkspaceAnswer

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
    responses=documented_errors(
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    ),
)
async def query_workspace(
    workspace_id: UUID, data: ChatRequest, user: CurrentUser, session: SessionDep, ai: AIServiceDep
) -> WorkspaceAnswer:
    """Ask a question in an owned workspace and return the answer with citations."""
    workspace = await workspace_service.get_owned_workspace(
        session=session,
        workspace_id=workspace_id,
        owner_id=user.id,
    )
    if workspace is None:
        raise NotFoundError("Workspace not found")

    messages = await chat_history_service.list_recent_messages(
        session=session,
        workspace_id=workspace.id,
    )
    history = [
        ChatMessage(role=ChatRole(message.role.value), content=message.content)
        for message in messages
    ]
    answer = await ai.ask_workspace(
        workspace_id=workspace.id,
        query=data.question,
        chat_history=history,
    )
    await chat_history_service.save_turn(
        session=session,
        workspace_id=workspace.id,
        question=data.question,
        answer=answer,
    )

    return answer


@router.get(
    "/{workspace_id}/chat/history",
    response_model=list[WorkspaceChatMessageRead],
    responses=documented_errors(HTTPStatus.NOT_FOUND),
)
async def messages_list(
    session: SessionDep,
    workspace_id: UUID,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Sequence[WorkspaceChatMessage]:
    """List the workspace's chat messages in chronological order."""
    workspace = await workspace_service.get_owned_workspace(
        session=session,
        workspace_id=workspace_id,
        owner_id=user.id,
    )
    if workspace is None:
        raise NotFoundError("Workspace not found")
    return await chat_history_service.list_messages(
        session=session, workspace_id=workspace.id, offset=offset, limit=limit
    )


@router.delete(
    "/{workspace_id}/chat/history",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=documented_errors(HTTPStatus.NOT_FOUND),
)
async def clear_history(session: SessionDep, workspace_id: UUID, user: CurrentUser) -> None:
    """Clear chat messages in an owned workspace."""
    workspace = await workspace_service.get_owned_workspace(
        session=session,
        workspace_id=workspace_id,
        owner_id=user.id,
    )
    if workspace is None:
        raise NotFoundError("Workspace not found")
    await chat_history_service.clear_messages(session=session, workspace_id=workspace.id)


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
