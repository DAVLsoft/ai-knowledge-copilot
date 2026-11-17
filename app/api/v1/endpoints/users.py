from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep
from app.models.user import User
from app.schemas.user import UserCreate, UserRead
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserRead)
async def create_user(data: UserCreate, session: SessionDep) -> User:
    try:
        return await user_service.create_user(session, data)
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists") from exc


@router.get("", response_model=list[UserRead])
async def list_users(
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Sequence[User]:
    return await user_service.list_users(session, offset=offset, limit=limit)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, session: SessionDep) -> User:
    user = await user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user
