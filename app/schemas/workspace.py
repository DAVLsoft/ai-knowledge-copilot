from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.chat_message import ChatMessageRole
from app.services.ai import SourceCitation


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WorkspaceChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: ChatMessageRole
    content: str
    sources: list[SourceCitation]
    created_at: datetime
