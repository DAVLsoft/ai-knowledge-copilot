"""AI/RAG service: contract, schemas, errors and a deterministic stub.

Routes should import from here and depend on `AIServiceDep` (app.api.deps).
"""

from app.services.ai.exceptions import (
    AIServiceError,
    DocumentParsingError,
    EmbeddingServiceError,
    LLMGenerationError,
    LLMTimeoutError,
)
from app.services.ai.interface import AIService
from app.services.ai.schemas import (
    ChatMessage,
    ChatRole,
    IngestionResult,
    SourceCitation,
    WorkspaceAnswer,
)
from app.services.ai.stub import SUPPORTED_EXTENSIONS, StubAIService

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AIService",
    "AIServiceError",
    "ChatMessage",
    "ChatRole",
    "DocumentParsingError",
    "EmbeddingServiceError",
    "IngestionResult",
    "LLMGenerationError",
    "LLMTimeoutError",
    "SourceCitation",
    "StubAIService",
    "WorkspaceAnswer",
]
