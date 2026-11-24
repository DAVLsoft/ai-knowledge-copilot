from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.services.ai.schemas import ChatMessage, IngestionResult, WorkspaceAnswer


class AIService(Protocol):
    async def ingest_document(
        self,
        *,
        document_id: UUID,
        workspace_id: UUID,
        file_path: str | Path,
        filename: str,
    ) -> IngestionResult: ...

    async def ask_workspace(
        self,
        *,
        workspace_id: UUID,
        query: str,
        chat_history: Sequence[ChatMessage] = (),
    ) -> WorkspaceAnswer: ...
