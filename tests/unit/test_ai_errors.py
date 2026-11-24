from http import HTTPStatus

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.exception_handlers import register_exception_handlers
from app.services.ai import (
    AIServiceError,
    DocumentParsingError,
    EmbeddingServiceError,
    LLMGenerationError,
    LLMTimeoutError,
)


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (DocumentParsingError(), HTTPStatus.UNPROCESSABLE_ENTITY, "Document could not be parsed"),
        (
            DocumentParsingError("Document is empty"),
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "Document is empty",
        ),
        (EmbeddingServiceError(), HTTPStatus.BAD_GATEWAY, "Embedding service is unavailable"),
        (LLMGenerationError(), HTTPStatus.BAD_GATEWAY, "Failed to generate an answer"),
        (LLMTimeoutError(), HTTPStatus.GATEWAY_TIMEOUT, "The AI model did not respond in time"),
        (AIServiceError(), HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error"),
    ],
    ids=["parsing", "parsing-custom", "embedding", "llm", "llm-timeout", "base"],
)
async def test_ai_errors_map_to_http_responses(
    error: AIServiceError, status: HTTPStatus, detail: str
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_timeout_is_also_a_generation_error() -> None:
    assert isinstance(LLMTimeoutError(), LLMGenerationError)
