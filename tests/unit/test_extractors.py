from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from app.services.ai import (
    SUPPORTED_EXTENSIONS,
    DocumentParsingError,
    DocxExtractor,
    ExtractedSegment,
    PdfExtractor,
    PlainTextExtractor,
    extract_document,
    extract_segments_from_bytes,
    normalize_text,
)
from tests.unit.document_builders import build_docx_bytes, build_pdf_bytes

OLE2_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def test_supported_extensions_include_pdf_txt_docx_and_md() -> None:
    assert frozenset({".pdf", ".txt", ".docx", ".md"}) == SUPPORTED_EXTENSIONS


def test_normalize_text_cleans_unicode_whitespace_and_control_artifacts() -> None:
    messy = "\ufeffHello\u200b   \t\uff57orld\r\n\r\n\r\n\r\nSecond   line  \rThird line\x00"
    assert normalize_text(messy) == "Hello world\n\nSecond line\nThird line"


@pytest.mark.parametrize(
    ("filename", "raw_bytes", "expected"),
    [
        ("notes.txt", b"Simple UTF-8 text", "Simple UTF-8 text"),
        ("bom.txt", "UTF-8 BOM text".encode("utf-8-sig"), "UTF-8 BOM text"),
        ("utf16le.txt", b"\xff\xfe" + "UTF-16 LE text".encode("utf-16-le"), "UTF-16 LE text"),
        ("utf16be.txt", b"\xfe\xff" + "UTF-16 BE text".encode("utf-16-be"), "UTF-16 BE text"),
        ("utf32le.txt", b"\xff\xfe\x00\x00" + "UTF-32 text".encode("utf-32-le"), "UTF-32 text"),
        (
            "cyrillic.txt",
            "Привіт, це документ українською мовою".encode("cp1251"),
            "Привіт, це документ українською мовою",
        ),
        (
            "western.txt",
            "Café résumé — price 100€".encode("cp1252"),
            "Café résumé — price 100€",
        ),
        (
            "readme.md",
            b"# Architecture\r\n\r\nService uses FastAPI and PostgreSQL.",
            "# Architecture\n\nService uses FastAPI and PostgreSQL.",
        ),
    ],
)
async def test_plain_text_and_markdown_extraction_with_encoding_fallback(
    tmp_path: Path, filename: str, raw_bytes: bytes, expected: str
) -> None:
    path = tmp_path / filename
    path.write_bytes(raw_bytes)

    segments = await extract_document(path, filename)

    assert segments == [ExtractedSegment(text=expected, page_number=None)]


async def test_pdf_extraction_preserves_one_based_page_numbers_and_skips_blank_pages(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(build_pdf_bytes(["Chapter 1 Intro", "", "Chapter 3 Finale"]))

    segments = await extract_document(pdf_path, "manual.pdf")

    assert segments == [
        ExtractedSegment(text="Chapter 1 Intro", page_number=1),
        ExtractedSegment(text="Chapter 3 Finale", page_number=3),
    ]


async def test_docx_extraction_preserves_reading_order_of_paragraphs_and_tables(
    tmp_path: Path,
) -> None:
    docx_path = tmp_path / "spec.docx"
    docx_path.write_bytes(
        build_docx_bytes(
            paragraphs=["Executive Summary", "Detailed Requirements"],
            table_rows=[["Role", "Access"], ["Admin", "Full"]],
        )
    )

    segments = await extract_document(docx_path, "spec.docx")

    assert len(segments) == 1
    assert segments[0].page_number is None
    assert segments[0].text == (
        "Executive Summary\n\nDetailed Requirements\n\nRole | Access\n\nAdmin | Full"
    )


@pytest.mark.parametrize(
    ("filename", "payload", "match"),
    [
        ("empty.txt", b"", "empty"),
        ("spaces.txt", b"   \r\n\t  ", "no extractable text"),
        ("empty.md", b"", "empty"),
        ("binary.txt", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "binary"),
        ("utf8_ctrl.txt", b"valid utf8 with \x07 bell", "binary"),
        ("bad_utf16.txt", b"\xff\xfe\x41", "invalid UTF-16"),
        ("bad_utf32.txt", b"\xff\xfe\x00\x00\x41\x00", "invalid UTF-32"),
        ("null_utf16.txt", b"\xff\xfe\x41\x00\x00\x00\x42\x00", "binary"),
        ("empty.pdf", b"", "empty"),
        ("corrupted.pdf", b"%PDF-1.7\nnot a real xref", "corrupted or not a valid PDF"),
        ("blank_pages.pdf", build_pdf_bytes(["", ""]), "no extractable text"),
        (
            "protected.pdf",
            build_pdf_bytes(["Confidential"], password="s3cret"),  # noqa: S106
            "password-protected",
        ),
        ("empty.docx", b"", "empty"),
        ("corrupted.docx", b"PK\x03\x04corrupted zip payload", "corrupted or not a valid DOCX"),
        ("legacy_ole.docx", OLE2_HEADER + b"\x00" * 64, "corrupted or not a valid DOCX"),
        (
            "protected.docx",
            OLE2_HEADER + b"\x00" * 32 + "EncryptedPackage".encode("utf-16-le"),
            "password-protected",
        ),
        ("empty_body.docx", build_docx_bytes(paragraphs=["   "]), "no extractable text"),
        ("archive.zip", b"PK\x03\x04", "Unsupported file type"),
    ],
)
def test_extractors_raise_document_parsing_error_on_invalid_inputs(
    filename: str, payload: bytes, match: str
) -> None:
    with pytest.raises(DocumentParsingError, match=match):
        extract_segments_from_bytes(payload, filename)


async def test_extract_document_rejects_unsupported_and_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(DocumentParsingError, match="Unsupported file type"):
        await extract_document(tmp_path / "photo.jpg", "photo.jpg")

    with pytest.raises(DocumentParsingError, match="could not be read"):
        await extract_document(tmp_path / "does_not_exist.pdf", "does_not_exist.pdf")


def test_pdf_extractor_handles_page_level_decryption_and_corruption_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor = PdfExtractor()

    encrypted_page = MagicMock()
    encrypted_page.extract_text.side_effect = FileNotDecryptedError("encrypted")
    fake_reader = MagicMock(is_encrypted=False, pages=[encrypted_page])
    monkeypatch.setattr("app.services.ai.extractors.PdfReader", lambda *_a, **_k: fake_reader)
    with pytest.raises(DocumentParsingError, match="password-protected"):
        extractor.extract(b"%PDF-1.7")

    broken_page = MagicMock()
    broken_page.extract_text.side_effect = RuntimeError("malformed stream")
    fake_reader_broken = MagicMock(is_encrypted=False, pages=[broken_page])
    monkeypatch.setattr(
        "app.services.ai.extractors.PdfReader", lambda *_a, **_k: fake_reader_broken
    )
    with pytest.raises(DocumentParsingError, match="corrupted"):
        extractor.extract(b"%PDF-1.7")

    empty_reader = MagicMock(is_encrypted=False, pages=[])
    monkeypatch.setattr("app.services.ai.extractors.PdfReader", lambda *_a, **_k: empty_reader)
    with pytest.raises(DocumentParsingError, match="no extractable text"):
        extractor.extract(b"%PDF-1.7")


def test_plain_text_and_docx_direct_extractors_are_reusable() -> None:
    assert PlainTextExtractor().extract(b"hello")[0].text == "hello"
    assert DocxExtractor().extract(build_docx_bytes(["hello"]))[0].text == "hello"
    assert PdfReader is not None
