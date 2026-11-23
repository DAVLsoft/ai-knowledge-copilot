from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import User
from app.services.rag import RAGService, UnconfiguredRAGService
from app.services.storage import LocalStorage, StorageBackend
from app.services.user import get_user

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_storage() -> StorageBackend:
    return LocalStorage(settings.upload_dir)


def get_rag_service() -> RAGService:
    return UnconfiguredRAGService()


StorageDep = Annotated[StorageBackend, Depends(get_storage)]
RAGDep = Annotated[RAGService, Depends(get_rag_service)]

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    unauthorized = UnauthorizedError("Could not validate credentials")
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise unauthorized from exc
    user = await get_user(session, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
