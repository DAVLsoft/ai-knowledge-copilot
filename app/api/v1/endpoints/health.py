from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep
from app.schemas.health import HealthRead

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def liveness() -> HealthRead:
    return HealthRead()


@router.get("/db")
async def readiness(session: SessionDep) -> HealthRead:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable") from exc
    return HealthRead()
