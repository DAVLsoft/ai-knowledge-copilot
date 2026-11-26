import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.errors import BadRequestError
from app.services.ai.chunker import RecursiveTextChunker
from app.services.ai.exceptions import AIServiceError, DocumentParsingError
from app.services.ai.extractors import SUPPORTED_EXTENSIONS, extract_document
from app.services.ai.schemas import (
    ChatMessage,
    DocumentChunk,
    IngestionResult,
    SourceCitation,
    WorkspaceAnswer,
)

_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class _IndexedChunk:
    chunk: DocumentChunk
    words: frozenset[str]


def _words(text: str) -> frozenset[str]:
    return frozenset(word.casefold() for word in _WORD.findall(text))


class StubAIService:
    def __init__(
        self,
        *,
        chunk_size: int = 256,
        chunk_overlap: int = 32,
        top_k: int = 3,
        snippet_length: int = 200,
    ) -> None:
        if min(chunk_size, top_k, snippet_length) < 1:
            raise ValueError("chunk_size, top_k and snippet_length must be positive")
        self._chunker = RecursiveTextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k
        self.snippet_length = snippet_length
        self.failure: AIServiceError | None = None
        self.queries: list[tuple[UUID, str]] = []
        self._index: dict[UUID, dict[UUID, list[_IndexedChunk]]] = {}

    def indexed_documents(self, workspace_id: UUID) -> set[UUID]:
        return set(self._index.get(workspace_id, {}))

    def get_document_chunks(self, workspace_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        return [item.chunk for item in self._index.get(workspace_id, {}).get(document_id, [])]

    async def ingest_document(
        self,
        *,
        document_id: UUID,
        workspace_id: UUID,
        file_path: str | Path,
        filename: str,
    ) -> IngestionResult:
        self._raise_injected_failure()
        segments = await extract_document(file_path, filename)
        chunks = self._chunker.chunk_segments(
            segments,
            workspace_id=workspace_id,
            document_id=document_id,
            filename=filename,
        )
        if not chunks:
            raise DocumentParsingError("Document contains no extractable text")

        indexed = [
            _IndexedChunk(chunk=chunk, words=_words(chunk.text))
            for chunk in chunks
            if _words(chunk.text)
        ]
        if not indexed:
            raise DocumentParsingError("Document contains no searchable words")

        self._index.setdefault(workspace_id, {})[document_id] = indexed
        return IngestionResult(
            document_id=document_id, workspace_id=workspace_id, chunk_count=len(indexed)
        )

    async def ask_workspace(
        self,
        *,
        workspace_id: UUID,
        query: str,
        chat_history: Sequence[ChatMessage] = (),
    ) -> WorkspaceAnswer:
        self._raise_injected_failure()
        query_words = _words(query)
        if not query_words:
            raise BadRequestError("Query must contain at least one word")
        self.queries.append((workspace_id, query))

        scored = [
            (len(query_words & item.words) / len(query_words), item.chunk)
            for items in self._index.get(workspace_id, {}).values()
            for item in items
            if query_words & item.words
        ]
        scored.sort(
            key=lambda pair: (
                -pair[0],
                pair[1].filename,
                str(pair[1].document_id),
                pair[1].chunk_index,
            )
        )
        citations = [
            SourceCitation(
                document_id=chunk.document_id,
                filename=chunk.filename,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                snippet=chunk.text[: self.snippet_length],
                score=round(score, 4),
            )
            for score, chunk in scored[: self.top_k]
        ]
        return WorkspaceAnswer(answer=self._answer(citations, chat_history), citations=citations)

    def _raise_injected_failure(self) -> None:
        if self.failure is not None:
            raise self.failure

    @staticmethod
    def _answer(citations: list[SourceCitation], chat_history: Sequence[ChatMessage]) -> str:
        context = f" Considered {len(chat_history)} previous message(s)." if chat_history else ""
        if not citations:
            return "No relevant information found in this workspace's documents." + context
        top = citations[0]
        return (
            f"Found {len(citations)} relevant passage(s). "
            f"Most relevant, from {top.filename}: {top.snippet}" + context
        )


__all__ = ["SUPPORTED_EXTENSIONS", "StubAIService"]
