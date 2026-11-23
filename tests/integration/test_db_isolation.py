from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

PAYLOAD = {"email": "iso@example.com", "password": "S3cure-pass-123", "full_name": "Iso"}


async def test_readiness_uses_test_db(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/db")
    assert response.status_code == 200


async def test_commit_is_visible_within_test(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post("/api/v1/register", json=PAYLOAD)
    assert response.status_code == 201
    users = (await db_session.scalars(select(User))).all()
    assert [u.email for u in users] == [PAYLOAD["email"]]


async def test_previous_test_data_was_rolled_back(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await db_session.scalars(select(User))).all() == []
    response = await client.post("/api/v1/register", json=PAYLOAD)
    assert response.status_code == 201
