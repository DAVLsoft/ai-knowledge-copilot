from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionDep
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenRead
from app.schemas.user import UserRead
from app.services.auth import UserAlreadyExistsError, authenticate_user, register_user

router = APIRouter(tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserRead,
    responses={409: {"description": "Email already registered"}},
)
async def register(data: RegisterRequest, session: SessionDep) -> User:
    try:
        return await register_user(session, data)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc


@router.post("/login", responses={401: {"description": "Invalid credentials"}})
async def login(data: LoginRequest, session: SessionDep) -> TokenRead:
    user = await authenticate_user(session, data.email, data.password.get_secret_value())
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenRead(access_token=create_access_token(user.id))
