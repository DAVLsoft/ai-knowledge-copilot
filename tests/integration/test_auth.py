import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from tests.integration.factories import PASSWORD, create_user


async def test_register_creates_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/register",
        json={
            "email": "New.User@Example.com",
            "username": "newuser",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.user@example.com"
    assert body["username"] == "newuser"
    assert body["is_active"] is True
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_duplicate_email_returns_409(client: AsyncClient) -> None:
    payload = {
        "email": "dup@example.com",
        "username": "duplicate",
        "password": PASSWORD,
    }
    assert (await client.post("/api/v1/register", json=payload)).status_code == 201

    response = await client.post(
        "/api/v1/register",
        json={**payload, "username": "another"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Username or email already registered"}


async def test_login_returns_working_token(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)

    response = await client.post(
        "/api/v1/login",
        data={"username": user.email, "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"  # noqa: S105
    assert decode_access_token(body["access_token"]) == user.id


@pytest.mark.parametrize(
    ("email", "password", "is_active"),
    [
        ("user@example.com", "wrong-password", True),
        ("missing@example.com", PASSWORD, True),
        ("user@example.com", PASSWORD, False),
    ],
    ids=["wrong-password", "unknown-user", "inactive-user"],
)
async def test_login_rejects_bad_credentials(
    client: AsyncClient, db_session: AsyncSession, email: str, password: str, is_active: bool
) -> None:
    await create_user(db_session, is_active=is_active)

    response = await client.post(
        "/api/v1/login",
        data={"username": email, "password": password},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
