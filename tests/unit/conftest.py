from collections.abc import Iterator
from typing import NoReturn

import pytest

from app.db.session import get_session
from app.main import app


@pytest.fixture(autouse=True)
def _forbid_database() -> Iterator[None]:
    """Unit tests must not touch the DB: fail loudly if a route asks for a session."""

    async def _no_db() -> NoReturn:
        raise RuntimeError("Unit tests must not use the database; use tests/integration")

    app.dependency_overrides[get_session] = _no_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)
