from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, status

from app.api.deps import SessionDep
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenRead
from app.schemas.user import UserRead
from app.services.auth import UserAlreadyExistsError, authenticate_user, register_user


class LoginForm:
    def __init__(
        self,
        username: str = Form(),
        password: str = Form(),
    ):
        self.username = username
        self.password = password


router = APIRouter(tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserRead,
    responses={409: {"description": "Username or email already registered"}},
)
async def register(data: RegisterRequest, session: SessionDep) -> User:
    try:
        return await register_user(session, data)
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Username or email already registered"
        ) from exc


@router.post("/login", responses={401: {"description": "Invalid username or password"}})
async def login(
    form_data: Annotated[LoginForm, Depends()],
    session: SessionDep,
) -> TokenRead:
    user = await authenticate_user(
        session,
        form_data.username,
        form_data.password,
    )
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenRead(access_token=create_access_token(user.id))
