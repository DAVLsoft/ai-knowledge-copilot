from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessageRole, WorkspaceChatMessage
from app.services.ai import WorkspaceAnswer


async def list_messages(
    session: AsyncSession, *, workspace_id: UUID, offset: int = 0, limit: int = 50
) -> Sequence[WorkspaceChatMessage]:
    result = await session.scalars(
        select(WorkspaceChatMessage)
        .where(WorkspaceChatMessage.workspace_id == workspace_id)
        .order_by(WorkspaceChatMessage.id)
        .offset(offset)
        .limit(limit)
    )
    return result.all()


async def list_recent_messages(
    session: AsyncSession, *, workspace_id: UUID
) -> Sequence[WorkspaceChatMessage]:
    result = await session.scalars(
        select(WorkspaceChatMessage)
        .where(WorkspaceChatMessage.workspace_id == workspace_id)
        .order_by(WorkspaceChatMessage.id.desc())
        .limit(50)
    )
    return list(reversed(result.all()))


async def save_turn(
    session: AsyncSession, *, workspace_id: UUID, question: str, answer: WorkspaceAnswer
) -> None:
    user_message = WorkspaceChatMessage(
        workspace_id=workspace_id,
        role=ChatMessageRole.USER,
        content=question,
        sources=[],
    )

    assistant_message = WorkspaceChatMessage(
        workspace_id=workspace_id,
        role=ChatMessageRole.ASSISTANT,
        content=answer.answer,
        sources=[citation.model_dump(mode="json") for citation in answer.citations],
    )

    session.add_all([user_message, assistant_message])
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise


async def clear_messages(session: AsyncSession, *, workspace_id: UUID) -> None:
    try:
        await session.execute(
            delete(WorkspaceChatMessage).where(WorkspaceChatMessage.workspace_id == workspace_id)
        )
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
