from collections.abc import Sequence
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.core.errors import NotFoundError
from app.exception_handlers import documented_errors
from app.models.user import User
from app.schemas.user import UserRead
from app.services import user as user_service

router = APIRouter(
    prefix="/users", tags=["users"], responses=documented_errors(HTTPStatus.UNPROCESSABLE_ENTITY)
)


@router.get("", response_model=list[UserRead])
async def list_users(
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Sequence[User]:
    return await user_service.list_users(session, offset=offset, limit=limit)


@router.get(
    "/{user_id}", response_model=UserRead, responses=documented_errors(HTTPStatus.NOT_FOUND)
)
async def get_user(user_id: UUID, session: SessionDep) -> User:
    user = await user_service.get_user(session, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user
