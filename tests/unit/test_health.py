from collections.abc import AsyncIterator

from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_session
from app.main import app


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _Session:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def execute(self, *_: object) -> None:
        if self.error:
            raise self.error


def _use_session(session: _Session) -> None:
    async def _get_session() -> AsyncIterator[_Session]:
        yield session

    app.dependency_overrides[get_session] = _get_session


async def test_readiness_ok(client: AsyncClient) -> None:
    _use_session(_Session())
    response = await client.get("/api/v1/health/db")
    assert response.status_code == 200


async def test_readiness_returns_503_when_db_down(client: AsyncClient) -> None:
    _use_session(_Session(SQLAlchemyError("boom")))
    response = await client.get("/api/v1/health/db")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
