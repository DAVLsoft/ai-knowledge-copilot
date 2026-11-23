import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token
from tests.integration.factories import create_user


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_valid_token_returns_user(db_session: AsyncSession) -> None:
    user = await create_user(db_session)

    current = await get_current_user(db_session, _bearer(create_access_token(user.id)))

    assert current.id == user.id


async def test_inactive_user_is_rejected(db_session: AsyncSession) -> None:
    user = await create_user(db_session, is_active=False)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, _bearer(create_access_token(user.id)))

    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "token", [None, "not-a-jwt", "a.b.c"], ids=["missing", "garbage", "malformed"]
)
async def test_invalid_credentials_are_rejected(
    db_session: AsyncSession, token: str | None
) -> None:
    credentials = None if token is None else _bearer(token)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, credentials)

    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_token_for_deleted_user_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, _bearer(create_access_token(uuid.uuid4())))

    assert exc.value.status_code == 401
