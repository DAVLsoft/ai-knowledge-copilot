from http import HTTPStatus

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep
from app.core.errors import ServiceUnavailableError
from app.exception_handlers import documented_errors
from app.schemas.health import HealthRead

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def liveness() -> HealthRead:
    return HealthRead()


@router.get("/db", responses=documented_errors(HTTPStatus.SERVICE_UNAVAILABLE))
async def readiness(session: SessionDep) -> HealthRead:
    try:
        await session.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as exc:
        raise ServiceUnavailableError("Database unavailable") from exc
    return HealthRead()
