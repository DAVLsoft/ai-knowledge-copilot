from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RAGAnswer:
    answer: str
    source_document_ids: list[UUID] = field(default_factory=list)


class RAGService(Protocol):
    """Interface to the AI/RAG backend. Tests use a fake; no real LLM is ever called."""

    async def index_document(self, document_id: UUID, text: str) -> None: ...

    async def answer(self, workspace_id: UUID, question: str) -> RAGAnswer: ...


class UnconfiguredRAGService:
    """Placeholder until a real implementation is wired in."""

    async def index_document(self, document_id: UUID, text: str) -> None:
        raise NotImplementedError("RAG service is not configured")

    async def answer(self, workspace_id: UUID, question: str) -> RAGAnswer:
        raise NotImplementedError("RAG service is not configured")
