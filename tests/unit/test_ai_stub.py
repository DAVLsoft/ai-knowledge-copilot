import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.errors import BadRequestError
from app.services.ai import (
    AIService,
    ChatMessage,
    ChatRole,
    DocumentParsingError,
    LLMTimeoutError,
    SourceCitation,
    StubAIService,
)

WS = uuid.UUID("00000000-0000-0000-0000-00000000000a")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-00000000000b")


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


async def _ingest(
    ai: StubAIService, tmp_path: Path, name: str, content: str | bytes, ws: uuid.UUID = WS
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    await ai.ingest_document(
        document_id=doc_id,
        workspace_id=ws,
        file_path=_write(tmp_path, name, content),
        filename=name,
    )
    return doc_id


def test_stub_satisfies_protocol() -> None:
    service: AIService = StubAIService()
    assert service is not None


async def test_ingest_splits_text_into_word_boundary_chunks(tmp_path: Path) -> None:
    ai = StubAIService(chunk_size=11)
    path = _write(tmp_path, "notes.txt", "alpha beta gamma delta epsilon")
    doc_id = uuid.uuid4()

    result = await ai.ingest_document(
        document_id=doc_id, workspace_id=WS, file_path=str(path), filename="notes.txt"
    )

    assert (result.document_id, result.workspace_id, result.chunk_count) == (doc_id, WS, 3)
    assert ai.indexed_documents(WS) == {doc_id}


async def test_ask_returns_ranked_citations_from_ingested_documents(tmp_path: Path) -> None:
    ai = StubAIService()
    vacation = await _ingest(ai, tmp_path, "policy.md", "Vacation policy: 20 paid vacation days.")
    await _ingest(ai, tmp_path, "security.txt", "Rotate passwords every 90 days.")

    answer = await ai.ask_workspace(workspace_id=WS, query="How many vacation days?")

    assert [c.filename for c in answer.citations] == ["policy.md", "security.txt"]
    top = answer.citations[0]
    assert top == SourceCitation(
        document_id=vacation,
        filename="policy.md",
        chunk_index=0,
        page_number=None,
        snippet="Vacation policy: 20 paid vacation days.",
        score=0.5,
    )
    assert answer.citations[1].score == 0.25
    assert "policy.md" in answer.answer
    assert ai.queries == [(WS, "How many vacation days?")]


async def test_ask_is_deterministic(tmp_path: Path) -> None:
    ai = StubAIService()
    await _ingest(ai, tmp_path, "a.txt", "deterministic answers are nice")
    first = await ai.ask_workspace(workspace_id=WS, query="deterministic")
    second = await ai.ask_workspace(workspace_id=WS, query="deterministic")
    assert first == second


async def test_ask_respects_top_k_and_snippet_length(tmp_path: Path) -> None:
    ai = StubAIService(top_k=2, snippet_length=5)
    for i in range(3):
        await _ingest(ai, tmp_path, f"doc{i}.txt", f"shared word number{i}")

    answer = await ai.ask_workspace(workspace_id=WS, query="shared")

    assert len(answer.citations) == 2
    assert all(len(c.snippet) == 5 for c in answer.citations)


async def test_workspaces_are_isolated(tmp_path: Path) -> None:
    ai = StubAIService()
    await _ingest(ai, tmp_path, "secret.txt", "top secret roadmap", ws=OTHER_WS)

    answer = await ai.ask_workspace(workspace_id=WS, query="roadmap")

    assert answer.citations == []
    assert answer.answer.startswith("[stub] No relevant information")


async def test_reingest_replaces_previous_chunks(tmp_path: Path) -> None:
    ai = StubAIService()
    doc_id = uuid.uuid4()
    for content in ("old content", "new content"):
        path = _write(tmp_path, "doc.txt", content)
        await ai.ingest_document(
            document_id=doc_id, workspace_id=WS, file_path=path, filename="doc.txt"
        )

    assert (await ai.ask_workspace(workspace_id=WS, query="old")).citations == []
    assert len((await ai.ask_workspace(workspace_id=WS, query="new")).citations) == 1


async def test_chat_history_is_acknowledged(tmp_path: Path) -> None:
    ai = StubAIService()
    history = [
        ChatMessage(role=ChatRole.USER, content="hi"),
        ChatMessage(role=ChatRole.ASSISTANT, content="hello"),
    ]
    answer = await ai.ask_workspace(workspace_id=WS, query="anything", chat_history=history)
    assert "Considered 2 previous message(s)." in answer.answer


async def test_pdf_gets_placeholder_text_on_page_one(tmp_path: Path) -> None:
    ai = StubAIService()
    await _ingest(ai, tmp_path, "report.pdf", b"%PDF-1.7\n...")

    answer = await ai.ask_workspace(workspace_id=WS, query="report.pdf")

    assert answer.citations[0].page_number == 1


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("image.png", b"\x89PNG", "Unsupported file type"),
        ("no_extension", b"data", "Unsupported file type"),
        ("empty.txt", b"  \n ", "Document is empty"),
        ("binary.txt", b"\xff\xfe\x00bad", "not valid UTF-8"),
        ("fake.pdf", b"not a pdf", "not a valid PDF"),
    ],
)
async def test_ingest_rejects_bad_documents(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    with pytest.raises(DocumentParsingError, match=message):
        await _ingest(StubAIService(), tmp_path, name, content)


async def test_ingest_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DocumentParsingError, match="could not be read"):
        await StubAIService().ingest_document(
            document_id=uuid.uuid4(),
            workspace_id=WS,
            file_path=tmp_path / "missing.txt",
            filename="missing.txt",
        )


async def test_ingest_rejects_text_without_words(tmp_path: Path) -> None:
    ai = StubAIService()
    # Non-empty bytes that decode to whitespace only (non-breaking spaces).
    with pytest.raises(DocumentParsingError, match="no text"):
        await _ingest(ai, tmp_path, "blank.txt", "\u00a0\u00a0")


async def test_ask_rejects_query_without_words() -> None:
    with pytest.raises(BadRequestError):
        await StubAIService().ask_workspace(workspace_id=WS, query="?!")


async def test_injected_failure_is_raised_by_every_call(tmp_path: Path) -> None:
    ai = StubAIService()
    ai.failure = LLMTimeoutError()

    with pytest.raises(LLMTimeoutError):
        await ai.ask_workspace(workspace_id=WS, query="hello")
    with pytest.raises(LLMTimeoutError):
        await _ingest(ai, tmp_path, "a.txt", "text")


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        StubAIService(top_k=0)


@pytest.mark.parametrize(
    "overrides",
    [{"score": 1.5}, {"score": -0.1}, {"chunk_index": -1}, {"page_number": 0}],
)
def test_source_citation_validation(overrides: dict[str, object]) -> None:
    data: dict[str, object] = {
        "document_id": uuid.uuid4(),
        "filename": "a.txt",
        "chunk_index": 0,
        "snippet": "x",
        "score": 0.5,
    } | overrides
    with pytest.raises(ValidationError):
        SourceCitation.model_validate(data)


def test_chat_message_requires_content() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role=ChatRole.USER, content="")
