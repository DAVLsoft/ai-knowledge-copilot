import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.factories import create_user


async def test_list_users_paginates(client: AsyncClient, db_session: AsyncSession) -> None:
    for i in range(3):
        await create_user(
            db_session,
            email=f"u{i}@example.com",
            username=f"user{i}",
        )

    everyone = await client.get("/api/v1/users")
    page = await client.get("/api/v1/users", params={"limit": 2, "offset": 1})

    assert everyone.status_code == 200
    assert len(everyone.json()) == 3
    assert [u["id"] for u in page.json()] == [u["id"] for u in everyone.json()][1:3]
    assert "hashed_password" not in everyone.json()[0]


async def test_list_users_validates_query(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users", params={"limit": 0})
    assert response.status_code == 422


async def test_get_user(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await create_user(db_session)

    response = await client.get(f"/api/v1/users/{user.id}")

    assert response.status_code == 200
    assert response.json()["email"] == user.email


async def test_get_unknown_user_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/users/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}
