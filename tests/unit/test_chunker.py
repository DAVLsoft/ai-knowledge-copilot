import uuid
from pathlib import Path

import pytest

from app.services.ai import (
    DocumentChunk,
    ExtractedSegment,
    RecursiveTextChunker,
    count_tokens,
    extract_document,
    tokenize,
)
from tests.unit.document_builders import build_docx_bytes, build_pdf_bytes

WS_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOC_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_tokenize_splits_long_words_and_reconstructs_text() -> None:
    text = "Short internationalization!"
    tokens = tokenize(text)
    assert "".join(tokens) == text
    assert count_tokens(text) == len(tokens)
    assert len(tokens) > 3  # "internationalization" is split into subword pieces


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"chunk_size": 0, "chunk_overlap": 0}, "chunk_size"),
        ({"chunk_size": 10, "chunk_overlap": -1}, "chunk_overlap"),
        ({"chunk_size": 10, "chunk_overlap": 10}, "chunk_overlap"),
        ({"chunk_size": 10, "chunk_overlap": 0, "separators": ()}, "separators"),
    ],
)
def test_chunker_validates_configuration(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        RecursiveTextChunker(**kwargs)  # type: ignore[arg-type]


def test_chunker_returns_empty_list_for_blank_text() -> None:
    chunker = RecursiveTextChunker(chunk_size=10, chunk_overlap=2)
    assert chunker.split_text("   \n\t  ") == []


def test_recursive_chunker_prefers_paragraph_and_sentence_boundaries_with_overlap() -> None:
    text = (
        "First paragraph has four words.\n\n"
        "Second paragraph has five nice words.\n\n"
        "Third paragraph finishes here."
    )
    chunker = RecursiveTextChunker(chunk_size=18, chunk_overlap=10)

    pieces = chunker.split_text(text)

    assert len(pieces) == 2
    assert "First paragraph has four words." in pieces[0]
    assert "Second paragraph has five nice words." in pieces[0]
    # Overlap carries the second paragraph into the start of the next chunk.
    assert pieces[1].startswith("Second paragraph has five nice words.")
    assert "Third paragraph finishes here." in pieces[1]
    assert all(count_tokens(piece) <= 18 for piece in pieces)


def test_recursive_chunker_splits_oversized_paragraph_by_sentences_keeping_periods() -> None:
    paragraph = "Alpha one two. Beta three four. Gamma five six."
    chunker = RecursiveTextChunker(chunk_size=6, chunk_overlap=0)

    pieces = chunker.split_text(paragraph)

    assert pieces == [
        "Alpha one two.",
        "Beta three four.",
        "Gamma five six.",
    ]


def test_recursive_chunker_falls_back_to_token_window_for_unsplittable_strings() -> None:
    continuous = "a" * 80  # 20 subword tokens of 4 chars, no whitespace or punctuation
    chunker = RecursiveTextChunker(chunk_size=6, chunk_overlap=2)

    pieces = chunker.split_text(continuous)

    assert len(pieces) == 5
    assert all(count_tokens(piece) <= 6 for piece in pieces)
    assert pieces[0] == "a" * 24
    assert pieces[1] == "a" * 24


def test_chunker_supports_custom_token_counter() -> None:
    # Custom counter: 1 word = 1 token regardless of word length.
    chunker = RecursiveTextChunker(
        chunk_size=3,
        chunk_overlap=1,
        token_counter=lambda s: len(s.split()),
    )
    pieces = chunker.split_text("internationalization configuration orchestration implementation")
    assert pieces == [
        "internationalization configuration orchestration",
        "orchestration implementation",
    ]


async def test_end_to_end_extraction_and_chunking_preserves_metadata_across_formats(
    tmp_path: Path,
) -> None:
    chunker = RecursiveTextChunker(chunk_size=8, chunk_overlap=2)

    # 1. Multi-page PDF -> sequential chunk_index + accurate page_number
    pdf_path = tmp_path / "handbook.pdf"
    pdf_path.write_bytes(
        build_pdf_bytes(
            [
                "One two three four five six seven eight nine ten.",
                "Final page summary.",
            ]
        )
    )
    pdf_segments = await extract_document(pdf_path, "handbook.pdf")
    pdf_chunks = chunker.chunk_segments(
        pdf_segments, workspace_id=WS_ID, document_id=DOC_ID, filename="handbook.pdf"
    )

    assert len(pdf_chunks) >= 3
    assert [c.chunk_index for c in pdf_chunks] == list(range(len(pdf_chunks)))
    assert pdf_chunks[0].page_number == 1
    assert pdf_chunks[-1].page_number == 2
    assert all(
        isinstance(c, DocumentChunk)
        and c.workspace_id == WS_ID
        and c.document_id == DOC_ID
        and c.filename == "handbook.pdf"
        and 1 <= c.token_count <= 8
        for c in pdf_chunks
    )

    # 2. DOCX -> page_number is None, sequential chunk_index
    docx_path = tmp_path / "policy.docx"
    docx_path.write_bytes(
        build_docx_bytes(
            paragraphs=["Security policy overview.", "Access control rules apply to every team."],
            table_rows=[["Team", "Tier"], ["Platform", "Tier 1"]],
        )
    )
    docx_chunks = chunker.chunk_segments(
        await extract_document(docx_path, "policy.docx"),
        workspace_id=WS_ID,
        document_id=DOC_ID,
        filename="policy.docx",
    )
    assert [c.chunk_index for c in docx_chunks] == list(range(len(docx_chunks)))
    assert all(c.page_number is None for c in docx_chunks)
    assert any("Platform | Tier 1" in c.text for c in docx_chunks)

    # 3. TXT & MD -> page_number is None, metadata preserved
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("Plain text note for chunking.", encoding="utf-8")
    txt_chunks = chunker.chunk_segments(
        await extract_document(txt_path, "notes.txt"),
        workspace_id=WS_ID,
        document_id=DOC_ID,
        filename="notes.txt",
    )
    assert len(txt_chunks) == 1
    assert txt_chunks[0] == DocumentChunk(
        workspace_id=WS_ID,
        document_id=DOC_ID,
        filename="notes.txt",
        chunk_index=0,
        page_number=None,
        text="Plain text note for chunking.",
        token_count=count_tokens("Plain text note for chunking."),
    )

    # Empty segments produce no chunks
    assert (
        chunker.chunk_segments(
            [ExtractedSegment(text="   ", page_number=1)],
            workspace_id=WS_ID,
            document_id=DOC_ID,
            filename="empty.pdf",
        )
        == []
    )
