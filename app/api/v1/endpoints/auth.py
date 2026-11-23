from http import HTTPStatus

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.core.security import create_access_token
from app.exception_handlers import documented_errors
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenRead
from app.schemas.user import UserRead
from app.services.auth import authenticate_user, register_user

router = APIRouter(tags=["auth"], responses=documented_errors(HTTPStatus.UNPROCESSABLE_ENTITY))


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserRead,
    responses=documented_errors(HTTPStatus.CONFLICT),
)
async def register(data: RegisterRequest, session: SessionDep) -> User:
    return await register_user(session, data)


@router.post("/login", responses=documented_errors(HTTPStatus.UNAUTHORIZED))
async def login(data: LoginRequest, session: SessionDep) -> TokenRead:
    user = await authenticate_user(session, data.email, data.password.get_secret_value())
    return TokenRead(access_token=create_access_token(user.id))
