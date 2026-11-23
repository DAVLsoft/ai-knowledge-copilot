import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.auth import RegisterRequest
from app.services.user import get_user_by_email, get_user_by_username


class UserAlreadyExistsError(Exception):
    pass


def is_duplicate_identity(exc: IntegrityError) -> bool:
    original = exc.orig
    cause = getattr(original, "__cause__", None)

    sqlstate = getattr(original, "sqlstate", None) or getattr(cause, "sqlstate", None)
    constraint_name = getattr(original, "constraint_name", None) or getattr(
        cause, "constraint_name", None
    )

    return sqlstate == "23505" and constraint_name in {"ix_users_email", "ix_users_username"}


async def register_user(session: AsyncSession, data: RegisterRequest) -> User:
    email = data.email.lower()

    if (
        await get_user_by_email(session, email)
        or await get_user_by_username(session, data.username)
        or await get_user_by_email(session, data.username)
        or await get_user_by_username(session, email)
    ):
        raise UserAlreadyExistsError

    user = User(
        id=uuid.uuid4(),
        email=email,
        username=data.username,
        hashed_password=await hash_password(data.password.get_secret_value()),
    )

    user.workspaces = [Workspace(name="My Workspace")]
    session.add(user)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_duplicate_identity(exc):
            raise UserAlreadyExistsError from exc
        raise

    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession,
    identifier: str,
    password: str,
) -> User | None:
    identifier = identifier.strip()

    if "@" in identifier:
        user = await get_user_by_email(session, identifier.lower())
    else:
        user = await get_user_by_username(session, identifier.lower())

    if user is None:
        return None

    valid_password = await verify_password(password, user.hashed_password)

    if not valid_password or not user.is_active:
        return None

    return user
