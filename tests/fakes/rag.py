from uuid import UUID

from app.services.rag import RAGAnswer


class FakeRAGService:
    """Deterministic RAGService that records calls for assertions."""

    def __init__(self, answer: str = "fake answer") -> None:
        self.canned_answer = answer
        self.indexed: list[tuple[UUID, str]] = []
        self.questions: list[tuple[UUID, str]] = []

    async def index_document(self, document_id: UUID, text: str) -> None:
        self.indexed.append((document_id, text))

    async def answer(self, workspace_id: UUID, question: str) -> RAGAnswer:
        self.questions.append((workspace_id, question))
        return RAGAnswer(
            answer=self.canned_answer, source_document_ids=[d for d, _ in self.indexed]
        )
