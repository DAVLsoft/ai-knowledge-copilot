class AppError(Exception):
    error_code = "internal_error"


class BadRequestError(AppError):
    error_code = "bad_request"


class UnauthorizedError(AppError):
    error_code = "unauthorized"


class ForbiddenError(AppError):
    error_code = "forbidden"


class NotFoundError(AppError):
    error_code = "not_found"


class ConflictError(AppError):
    error_code = "conflict"


class UserAlreadyExistsError(ConflictError):
    error_code = "user_already_exists"


class ServiceUnavailableError(AppError):
    error_code = "service_unavailable"
