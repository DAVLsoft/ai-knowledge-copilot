from typing import ClassVar

from app.core.errors import (
    AppError,
    BadGatewayError,
    GatewayTimeoutError,
    UnprocessableEntityError,
)


class AIServiceError(AppError):
    default_message: ClassVar[str] = "AI service error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class DocumentParsingError(AIServiceError, UnprocessableEntityError):
    default_message = "Document could not be parsed"


class EmbeddingServiceError(AIServiceError, BadGatewayError):
    default_message = "Embedding service is unavailable"


class LLMGenerationError(AIServiceError, BadGatewayError):
    default_message = "Failed to generate an answer"


class LLMTimeoutError(GatewayTimeoutError, LLMGenerationError):
    default_message = "The AI model did not respond in time"
