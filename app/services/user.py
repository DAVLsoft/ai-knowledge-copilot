from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


async def list_users(session: AsyncSession, *, offset: int = 0, limit: int = 50) -> Sequence[User]:
    result = await session.scalars(select(User).order_by(User.id).offset(offset).limit(limit))
    return result.all()
