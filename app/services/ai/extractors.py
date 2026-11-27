import asyncio
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from app.services.ai.exceptions import DocumentParsingError

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_DOCX_MARKERS = (
    b"EncryptedPackage",
    "EncryptedPackage".encode("utf-16-le"),
)
_ZERO_WIDTH_RE = re.compile(r"[\ufeff\u200b\u200c\u200d\u2060\x00]")
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_BINARY_CONTROL_RE = re.compile(rb"[\x00-\x08\x0e-\x1f]")
_CYRILLIC_RUN_RE = re.compile(rb"[\xc0-\xff]{3,}")


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    text: str
    page_number: int | None = None


class TextExtractor(Protocol):
    def extract(self, data: bytes) -> list[ExtractedSegment]: ...


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE_RE.sub(" ", line).strip() for line in normalized.split("\n")]
    collapsed = _BLANK_LINES_RE.sub("\n\n", "\n".join(lines))
    return collapsed.strip()


class PlainTextExtractor:
    def extract(self, data: bytes) -> list[ExtractedSegment]:
        if not data:
            raise DocumentParsingError("Document is empty")

        raw_text = self._decode(data)
        cleaned = normalize_text(raw_text)
        if not cleaned:
            raise DocumentParsingError("Document contains no extractable text")
        return [ExtractedSegment(text=cleaned, page_number=None)]

    @staticmethod
    def _decode(data: bytes) -> str:
        # 1. Explicit UTF-32 / UTF-16 BOMs first so BOM-prefixed files decode cleanly.
        if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            return PlainTextExtractor._decode_unicode_bom(data, "utf-32")
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return PlainTextExtractor._decode_unicode_bom(data, "utf-16")

        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = None

        if decoded is not None:
            if "\x00" in decoded or PlainTextExtractor._has_control_chars(decoded):
                raise DocumentParsingError("Document appears to be binary, not valid text")
            return decoded

        # Reject binary payloads before 8-bit single-byte fallbacks.
        if b"\x00" in data or _BINARY_CONTROL_RE.search(data):
            raise DocumentParsingError("Document appears to be binary, not valid text")

        encodings = (
            ("cp1251", "cp1252", "latin-1")
            if _CYRILLIC_RUN_RE.search(data)
            else ("cp1252", "cp1251", "latin-1")
        )
        for encoding in encodings:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue

        raise DocumentParsingError("Document text encoding is not supported")  # pragma: no cover

    @staticmethod
    def _decode_unicode_bom(data: bytes, encoding: str) -> str:
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError as exc:
            raise DocumentParsingError(f"Document has invalid {encoding.upper()} encoding") from exc
        if "\x00" in decoded or PlainTextExtractor._has_control_chars(decoded):
            raise DocumentParsingError("Document appears to be binary, not valid text")
        return decoded

    @staticmethod
    def _has_control_chars(text: str) -> bool:
        return any(ord(ch) < 32 and ch not in "\t\n\r\f\v" for ch in text)


class PdfExtractor:
    def extract(self, data: bytes) -> list[ExtractedSegment]:
        if not data or not data.strip():
            raise DocumentParsingError("Document is empty")

        try:
            reader = PdfReader(BytesIO(data), strict=False)
        except (PdfReadError, OSError, ValueError, TypeError) as exc:
            raise DocumentParsingError("Document is corrupted or not a valid PDF") from exc

        if reader.is_encrypted:
            raise DocumentParsingError("PDF document is password-protected")

        try:
            pages = list(reader.pages)
        except FileNotDecryptedError as exc:
            raise DocumentParsingError("PDF document is password-protected") from exc
        except (PdfReadError, KeyError, IndexError, ValueError, TypeError) as exc:
            raise DocumentParsingError("Document is corrupted or not a valid PDF") from exc

        if not pages:
            raise DocumentParsingError("Document contains no extractable text")

        segments: list[ExtractedSegment] = []
        for page_number, page in enumerate(pages, start=1):
            try:
                raw_page = page.extract_text() or ""
            except FileNotDecryptedError as exc:
                raise DocumentParsingError("PDF document is password-protected") from exc
            except Exception as exc:
                raise DocumentParsingError("Document is corrupted or not a valid PDF") from exc

            cleaned = normalize_text(raw_page)
            if cleaned:
                segments.append(ExtractedSegment(text=cleaned, page_number=page_number))

        if not segments:
            raise DocumentParsingError("Document contains no extractable text")

        return segments


class DocxExtractor:
    def extract(self, data: bytes) -> list[ExtractedSegment]:
        if not data or not data.strip():
            raise DocumentParsingError("Document is empty")

        if data.startswith(_OLE2_MAGIC):
            if any(marker in data for marker in _ENCRYPTED_DOCX_MARKERS):
                raise DocumentParsingError("DOCX document is password-protected")
            raise DocumentParsingError("Document is corrupted or not a valid DOCX")

        try:
            document = DocxDocument(BytesIO(data))
        except Exception as exc:
            raise DocumentParsingError("Document is corrupted or not a valid DOCX") from exc

        blocks: list[str] = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                if block.text.strip():
                    blocks.append(block.text)
            elif isinstance(block, Table):
                for row in block.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        blocks.append(" | ".join(cells))

        cleaned = normalize_text("\n\n".join(blocks))
        if not cleaned:
            raise DocumentParsingError("Document contains no extractable text")

        return [ExtractedSegment(text=cleaned, page_number=None)]


EXTRACTOR_REGISTRY: dict[str, TextExtractor] = {
    ".txt": PlainTextExtractor(),
    ".md": PlainTextExtractor(),
    ".pdf": PdfExtractor(),
    ".docx": DocxExtractor(),
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXTRACTOR_REGISTRY)


def extract_segments_from_bytes(data: bytes, filename: str) -> list[ExtractedSegment]:
    suffix = Path(filename).suffix.lower()
    extractor = EXTRACTOR_REGISTRY.get(suffix)
    if extractor is None:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentParsingError(f"Unsupported file type; allowed: {allowed}")
    return extractor.extract(data)


async def extract_document(file_path: str | Path, filename: str) -> list[ExtractedSegment]:
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTRACTOR_REGISTRY:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentParsingError(f"Unsupported file type; allowed: {allowed}")
    path = Path(file_path)
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        raise DocumentParsingError("Document file could not be read") from exc

    return await asyncio.to_thread(extract_segments_from_bytes, data, filename)
