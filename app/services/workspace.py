from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace


async def list_workspaces(
    session: AsyncSession, *, owner_id: UUID, offset: int = 0, limit: int = 50
) -> Sequence[Workspace]:
    result = await session.scalars(
        select(Workspace)
        .where(Workspace.owner_id == owner_id)
        .order_by(Workspace.created_at, Workspace.id)
        .offset(offset)
        .limit(limit)
    )

    return result.all()


async def create_workspace(session: AsyncSession, *, name: str, owner_id: UUID) -> Workspace:
    workspace = Workspace(name=name, owner_id=owner_id)

    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def get_owned_workspace(
    session: AsyncSession, *, workspace_id: UUID, owner_id: UUID
) -> Workspace | None:
    return await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == owner_id,
        )
    )
