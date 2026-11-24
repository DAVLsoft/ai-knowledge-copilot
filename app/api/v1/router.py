from http import HTTPStatus

from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, users, workspaces
from app.exception_handlers import documented_errors

api_router = APIRouter(responses=documented_errors(HTTPStatus.INTERNAL_SERVER_ERROR))
api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(users.router)
api_router.include_router(workspaces.router)
