from http import HTTPStatus

from fastapi import APIRouter

from app.api.v1.endpoints import auth, documents, health, users
from app.exception_handlers import documented_errors

api_router = APIRouter(
    responses=documented_errors(HTTPStatus.INTERNAL_SERVER_ERROR)
)

api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(health.router)
api_router.include_router(users.router)