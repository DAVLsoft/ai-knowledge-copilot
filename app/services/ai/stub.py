import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from docx import Document as DocxDocument

from app.core.errors import BadRequestError
from app.services.ai.exceptions import AIServiceError, DocumentParsingError
from app.services.ai.schemas import ChatMessage, IngestionResult, SourceCitation, WorkspaceAnswer

SUPPORTED_EXTENSIONS = frozenset({".txt", ".md", ".pdf", ".docx"})
_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class _Chunk:
    document_id: UUID
    filename: str
    chunk_index: int
    page_number: int | None
    text: str
    words: frozenset[str]


def _words(text: str) -> frozenset[str]:
    return frozenset(word.casefold() for word in _WORD.findall(text))


def _split(text: str, size: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        added = len(word) + (1 if current else 0)
        if current and length + added > size:
            chunks.append(" ".join(current))
            current, length = [word], len(word)
        else:
            current.append(word)
            length += added
    if current:
        chunks.append(" ".join(current))
    return chunks


class StubAIService:
    def __init__(self, *, chunk_size: int = 800, top_k: int = 3, snippet_length: int = 200) -> None:
        if min(chunk_size, top_k, snippet_length) < 1:
            raise ValueError("chunk_size, top_k and snippet_length must be positive")
        self.chunk_size = chunk_size
        self.top_k = top_k
        self.snippet_length = snippet_length
        self.failure: AIServiceError | None = None
        self.queries: list[tuple[UUID, str]] = []
        self._index: dict[UUID, dict[UUID, list[_Chunk]]] = {}

    def indexed_documents(self, workspace_id: UUID) -> set[UUID]:
        return set(self._index.get(workspace_id, {}))

    async def ingest_document(
        self,
        *,
        document_id: UUID,
        workspace_id: UUID,
        file_path: str | Path,
        filename: str,
    ) -> IngestionResult:
        self._raise_injected_failure()
        text, page_number = await self._extract_text(Path(file_path), filename)
        pieces = _split(text, self.chunk_size)
        if not pieces:
            raise DocumentParsingError("Document contains no text")
        self._index.setdefault(workspace_id, {})[document_id] = [
            _Chunk(document_id, filename, index, page_number, piece, _words(piece))
            for index, piece in enumerate(pieces)
        ]
        return IngestionResult(
            document_id=document_id, workspace_id=workspace_id, chunk_count=len(pieces)
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
            (len(query_words & chunk.words) / len(query_words), chunk)
            for chunks in self._index.get(workspace_id, {}).values()
            for chunk in chunks
            if query_words & chunk.words
        ]
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].filename,
                str(item[1].document_id),
                item[1].chunk_index,
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

    @staticmethod
    def _extract_docx_text(data: bytes) -> str:
        try:
            document = DocxDocument(BytesIO(data))
        except Exception as exc:
            raise DocumentParsingError("Document is not a valid DOCX file") from exc

        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]

        table_rows = [
            " ".join(cell.text for cell in row.cells)
            for table in document.tables
            for row in table.rows
            if any(cell.text.strip() for cell in row.cells)
        ]

        return "\n".join([*paragraphs, *table_rows])

    @staticmethod
    async def _extract_text(path: Path, filename: str) -> tuple[str, int | None]:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise DocumentParsingError(f"Unsupported file type; allowed: {allowed}")
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise DocumentParsingError("Document file could not be read") from exc
        if not data.strip():
            raise DocumentParsingError("Document is empty")
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise DocumentParsingError("File is not a valid PDF")
            # The stub does not parse PDFs; index a deterministic placeholder on page 1.
            return f"Stub text for {filename}. PDF content is not extracted by the stub.", 1
        if suffix == ".docx":
            return await asyncio.to_thread(StubAIService._extract_docx_text, data), None
        try:
            return data.decode("utf-8-sig"), None
        except UnicodeDecodeError as exc:
            raise DocumentParsingError("Document is not valid UTF-8 text") from exc
