import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User
from app.models.workspace import Workspace

PASSWORD = "S3cure-pass-123"  # noqa: S105 -- test fixture value


async def create_user(
    session: AsyncSession,
    *,
    email: str = "user@example.com",
    username: str = "testuser",
    password: str = PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        username=username,
        hashed_password=await hash_password(password),
        full_name="Test User",
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    return user


async def create_workspace(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str = "Test Workspace",
) -> Workspace:
    workspace = Workspace(
        name=name,
        owner_id=owner_id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace
