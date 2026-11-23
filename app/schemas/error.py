from pydantic import BaseModel


class ValidationIssue(BaseModel):
    loc: tuple[str | int, ...]
    type: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | None = None
