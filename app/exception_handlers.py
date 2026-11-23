import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.schemas.error import ErrorResponse, ValidationIssue

logger = logging.getLogger(__name__)

BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}
DOMAIN_STATUSES: dict[type[AppError], HTTPStatus] = {
    BadRequestError: HTTPStatus.BAD_REQUEST,
    UnauthorizedError: HTTPStatus.UNAUTHORIZED,
    ForbiddenError: HTTPStatus.FORBIDDEN,
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    ServiceUnavailableError: HTTPStatus.SERVICE_UNAVAILABLE,
}


def error_response(
    status: int, error: ErrorResponse, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=error.model_dump(exclude_none=True), headers=headers
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    for error_type, status in DOMAIN_STATUSES.items():
        if isinstance(exc, error_type):
            return error_response(
                status,
                ErrorResponse(code=exc.error_code, message=str(exc) or status.phrase),
                headers=BEARER_HEADERS if status == HTTPStatus.UNAUTHORIZED else None,
            )
    return await handle_unexpected_error(request, exc)


async def handle_validation_error(_: Request, exception: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exception)
    details = [ValidationIssue(loc=issue["loc"], type=issue["type"]) for issue in exc.errors()]
    return error_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        ErrorResponse(code="validation_error", message="Invalid request", details=details),
    )


async def handle_http_error(request: Request, exception: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exception)
    if exc.status_code == HTTPStatus.INTERNAL_SERVER_ERROR:
        return await handle_unexpected_error(request, exc)
    try:
        status = HTTPStatus(exc.status_code)
        code, message = status.name.lower(), status.phrase
    except ValueError:
        code, message = "http_error", "Request failed"
    return error_response(exc.status_code, ErrorResponse(code=code, message=message), exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled API exception during %s: %s", request.method, type(exc).__name__)
    return error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        ErrorResponse(code="internal_error", message="Internal server error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)


def documented_errors(*statuses: HTTPStatus) -> dict[int | str, dict[str, object]]:
    responses: dict[int | str, dict[str, object]] = {}
    for status in statuses:
        response: dict[str, object] = {"description": status.phrase, "model": ErrorResponse}
        if status == HTTPStatus.UNAUTHORIZED:
            response["headers"] = {
                "WWW-Authenticate": {"schema": {"type": "string"}, "example": "Bearer"}
            }
        responses[status] = response
    return responses
