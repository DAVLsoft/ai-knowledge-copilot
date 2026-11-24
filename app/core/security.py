from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

ALGORITHM = "HS256"


async def hash_password(password: str) -> str:
    hashed = await run_in_threadpool(bcrypt.hashpw, password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("ascii")


async def verify_password(password: str, hashed_password: str) -> bool:
    return await run_in_threadpool(
        bcrypt.checkpw, password.encode("utf-8"), hashed_password.encode("ascii")
    )


def create_access_token(user_id: UUID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> UUID:
    payload = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )
    try:
        return UUID(payload["sub"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTokenError("Invalid user identifier") from exc
