import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from jwt.exceptions import InvalidTokenError

from app.core.config import settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


async def test_password_hash_roundtrip() -> None:
    hashed = await hash_password("S3cure-pass-123")

    assert hashed != "S3cure-pass-123"
    assert await verify_password("S3cure-pass-123", hashed)
    assert not await verify_password("other", hashed)


def test_access_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id)) == user_id


def _encode(payload: dict[str, object]) -> str:
    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=ALGORITHM)


def test_expired_token_is_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = _encode({"sub": str(uuid.uuid4()), "iat": past, "exp": past + timedelta(minutes=1)})

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_with_bad_subject_is_rejected() -> None:
    now = datetime.now(UTC)
    token = _encode({"sub": "not-a-uuid", "iat": now, "exp": now + timedelta(minutes=5)})

    with pytest.raises(InvalidTokenError, match="Invalid user identifier"):
        decode_access_token(token)


def test_token_signed_with_other_key_is_rejected() -> None:
    now = datetime.now(UTC)
    payload = {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(payload, "x" * 32, algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)
