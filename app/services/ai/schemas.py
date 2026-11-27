from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """One previous turn of the conversation, oldest first in `chat_history`."""

    model_config = ConfigDict(frozen=True)
    role: ChatRole
    content: str = Field(min_length=1)


class DocumentChunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    workspace_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int = Field(ge=0, description="0-based position of the chunk in the document")
    page_number: int | None = Field(
        default=None, ge=1, description="1-based page; null for formats without pages"
    )
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)


class SourceCitation(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: UUID
    filename: str
    chunk_index: int = Field(ge=0, description="0-based position of the chunk in the document")
    page_number: int | None = Field(
        default=None, ge=1, description="1-based page; null for formats without pages"
    )
    snippet: str
    score: float = Field(ge=0.0, le=1.0, description="Relevance, higher is better")


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: UUID
    workspace_id: UUID
    chunk_count: int = Field(ge=1)


class WorkspaceAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)
    answer: str
    citations: list[SourceCitation] = Field(default_factory=list)
