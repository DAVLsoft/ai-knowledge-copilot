from http import HTTPStatus

from fastapi import HTTPException


class AppError(Exception):
    pass


class BadRequestError(AppError):
    pass


class UnauthorizedError(AppError, HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(AppError):
    pass


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class UserAlreadyExistsError(ConflictError):
    pass


class ServiceUnavailableError(AppError):
    pass
