import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.auth import RegisterRequest
from app.services.user import get_user_by_email


class UserAlreadyExistsError(Exception):
    pass


def is_duplicate_email(exc: IntegrityError) -> bool:
    original = exc.orig
    cause = getattr(original, "__cause__", None)
    return (
        getattr(original, "sqlstate", None) == "23505"
        and getattr(cause, "constraint_name", None) == "ix_users_email"
    )


async def register_user(session: AsyncSession, data: RegisterRequest) -> User:
    if await get_user_by_email(session, data.email) is not None:
        raise UserAlreadyExistsError
    user = User(
        id=uuid.uuid4(),
        email=data.email,
        hashed_password=await hash_password(data.password.get_secret_value()),
        full_name=data.full_name,
    )
    user.workspaces = [Workspace(name="My Workspace")]
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_duplicate_email(exc):
            raise UserAlreadyExistsError from exc
        raise
    await session.refresh(user)
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None:
        return None
    valid_password = await verify_password(password, user.hashed_password)
    if not valid_password or not user.is_active:
        return None
    return user
