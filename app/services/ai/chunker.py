import re
from collections.abc import Callable, Sequence
from uuid import UUID

from app.services.ai.extractors import ExtractedSegment
from app.services.ai.schemas import DocumentChunk

DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    ". ",
    "? ",
    "! ",
    "; ",
    ", ",
    " ",
    "",
)

# Pre-tokenization pattern: optional leading whitespace + word or single punctuation/symbol.
# Every character is preserved so joining token slices reconstructs exact substrings.
_PRETOKEN_RE = re.compile(r"\s*\w+|\s*[^\w\s]|\s+")
_MAX_SUBWORD_CHARS = 6
_SUBWORD_SLICE = 4


def tokenize(text: str) -> list[str]:
    """Split text into deterministic BPE-like subword tokens with lossless reconstruction."""
    tokens: list[str] = []
    for piece in _PRETOKEN_RE.findall(text):
        stripped = piece.lstrip()
        if not stripped:
            continue
        leading = piece[: len(piece) - len(stripped)]
        if len(stripped) > _MAX_SUBWORD_CHARS and stripped.isalnum():
            for idx in range(0, len(stripped), _SUBWORD_SLICE):
                prefix = leading if idx == 0 else ""
                tokens.append(prefix + stripped[idx : idx + _SUBWORD_SLICE])
        else:
            tokens.append(piece)
    return tokens


def count_tokens(text: str) -> int:
    return len(tokenize(text))


class RecursiveTextChunker:
    """Splits extracted segments into overlapping token-bounded `DocumentChunk` items."""

    def __init__(
        self,
        *,
        chunk_size: int = 256,
        chunk_overlap: int = 32,
        separators: Sequence[str] = DEFAULT_SEPARATORS,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must satisfy 0 <= chunk_overlap < chunk_size")
        if not separators:
            raise ValueError("separators must not be empty")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = tuple(separators)
        self._count_tokens = token_counter or count_tokens

    def split_text(self, text: str) -> list[str]:
        """Split a single text string into chunks respecting `chunk_size` and `chunk_overlap`."""
        cleaned = text.strip()
        if not cleaned:
            return []
        return self.split_recursive(cleaned, self.separators)

    def chunk_segments(
        self,
        segments: Sequence[ExtractedSegment],
        *,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> list[DocumentChunk]:
        """Chunk all segments of a document and attach sequential `chunk_index` metadata."""
        chunks: list[DocumentChunk] = []
        for segment in segments:
            for piece in self.split_text(segment.text):
                token_total = max(1, self._count_tokens(piece))
                chunks.append(
                    DocumentChunk(
                        workspace_id=workspace_id,
                        document_id=document_id,
                        filename=filename,
                        chunk_index=len(chunks),
                        page_number=segment.page_number,
                        text=piece,
                        token_count=token_total,
                    )
                )
        return chunks

    def split_recursive(self, text: str, separators: Sequence[str]) -> list[str]:
        if self._count_tokens(text) <= self.chunk_size:
            return [text]

        separator = ""
        remaining_separators: Sequence[str] = ()
        for idx, candidate in enumerate(separators):
            if candidate == "" or candidate in text:
                separator = candidate
                remaining_separators = separators[idx + 1 :]
                break

        if separator == "":
            return self.split_by_token_window(text)

        raw_parts, joiner = self.split_keeping_punctuation(text, separator)
        atomic_parts: list[str] = []
        for part in raw_parts:
            if not part:
                continue
            if self._count_tokens(part) <= self.chunk_size:
                atomic_parts.append(part)
            elif remaining_separators:
                atomic_parts.extend(self.split_recursive(part, remaining_separators))
            else:
                atomic_parts.extend(self.split_by_token_window(part))

        return self.merge_with_overlap(atomic_parts, joiner)

    @staticmethod
    def split_keeping_punctuation(text: str, separator: str) -> tuple[list[str], str]:
        if separator in {"\n\n", "\n", " "}:
            return [part.strip() for part in text.split(separator) if part.strip()], separator

        # Punctuation separators like ". " keep the punctuation mark on the left piece.
        punct = separator.rstrip()
        raw = text.split(separator)
        parts: list[str] = []
        for idx, item in enumerate(raw):
            piece = item.strip()
            if not piece:
                continue
            if idx < len(raw) - 1:
                piece = f"{piece}{punct}"
            parts.append(piece)
        return parts, " "

    def merge_with_overlap(self, parts: list[str], joiner: str) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []

        for part in parts:
            candidate = joiner.join([*current, part]) if current else part
            if current and self._count_tokens(candidate) > self.chunk_size:
                chunks.append(joiner.join(current))
                overlap_parts: list[str] = []
                for prev in reversed(current):
                    trial = joiner.join([prev, *overlap_parts]) if overlap_parts else prev
                    trial_with_next = joiner.join([trial, part])
                    if (
                        self._count_tokens(trial) <= self.chunk_overlap
                        and self._count_tokens(trial_with_next) <= self.chunk_size
                    ):
                        overlap_parts.insert(0, prev)
                    else:
                        break
                current = [*overlap_parts, part]
            else:
                current.append(part)

        if current:
            chunks.append(joiner.join(current))

        return chunks

    def split_by_token_window(self, text: str) -> list[str]:
        tokens = tokenize(text)
        if not tokens:
            return []
        step = max(1, self.chunk_size - self.chunk_overlap)
        windows: list[str] = []
        for start in range(0, len(tokens), step):
            window = "".join(tokens[start : start + self.chunk_size]).strip()
            if window:
                windows.append(window)
            if start + self.chunk_size >= len(tokens):
                break
        return windows
