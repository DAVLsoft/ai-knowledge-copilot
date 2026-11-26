from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.main import app
from app.models.chat_message import ChatMessageRole, WorkspaceChatMessage
from app.models.workspace import Workspace
from app.services.ai import ChatMessage, ChatRole, StubAIService
from tests.integration.factories import create_user


async def test_chat_persists_turns_and_sources_across_requests(
    client: AsyncClient, db_session: AsyncSession, fake_ai: StubAIService, tmp_path: Path
) -> None:
    owner = await create_user(db_session)
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    chat_path = f"/api/v1/workspaces/{workspace.id}/chat"
    source = tmp_path / "guide.txt"
    source.write_text("The guide is on the intranet.", encoding="utf-8")
    await fake_ai.ingest_document(
        document_id=uuid4(), workspace_id=workspace.id, file_path=source, filename=source.name
    )

    first = await client.post(chat_path, headers=headers, json={"question": "guide"})
    second = await client.post(chat_path, headers=headers, json={"question": "intranet"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["citations"]
    assert "Considered 2 previous message(s)." in second.json()["answer"]

    history = await client.get(f"{chat_path}/history", headers=headers)
    assert history.status_code == 200
    messages = history.json()
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "guide"),
        ("assistant", first.json()["answer"]),
        ("user", "intranet"),
        ("assistant", second.json()["answer"]),
    ]
    assert [message["sources"] for message in messages] == [
        [],
        first.json()["citations"],
        [],
        second.json()["citations"],
    ]
    page = await client.get(f"{chat_path}/history?offset=1&limit=2", headers=headers)
    assert page.status_code == 200
    assert page.json() == messages[1:3]


async def test_clear_history_preserves_workspace_and_other_histories(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await create_user(db_session)
    other = await create_user(db_session, email="other@example.com", username="other")
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    other_workspace = Workspace(name="Private", owner_id=other.id)
    db_session.add_all([workspace, other_workspace])
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    chat_path = f"/api/v1/workspaces/{workspace.id}/chat"
    other_path = f"/api/v1/workspaces/{other_workspace.id}/chat"

    assert (
        await client.post(chat_path, headers=headers, json={"question": "Guide?"})
    ).status_code == 200
    assert (
        await client.post(other_path, headers=other_headers, json={"question": "Private?"})
    ).status_code == 200

    for method in ("GET", "DELETE"):
        for workspace_id in (workspace.id, uuid4()):
            denied = await client.request(
                method, f"/api/v1/workspaces/{workspace_id}/chat/history", headers=other_headers
            )
            assert denied.status_code == 404
            assert denied.json() == {"detail": "Workspace not found"}

    owner_history = await client.get(f"{chat_path}/history", headers=headers)
    assert len(owner_history.json()) == 2
    cleared = await client.delete(f"{chat_path}/history", headers=headers)
    assert cleared.status_code == 204
    assert cleared.content == b""
    empty = await client.get(f"{chat_path}/history", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == []
    other_history = await client.get(f"{other_path}/history", headers=other_headers)
    assert other_history.status_code == 200
    assert len(other_history.json()) == 2

    fresh = await client.post(chat_path, headers=headers, json={"question": "Start over"})
    assert fresh.status_code == 200
    assert "previous message(s)" not in fresh.json()["answer"]


async def test_chat_passes_latest_messages_in_chronological_order(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_ai: StubAIService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await create_user(db_session)
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceChatMessage(
                workspace_id=workspace.id,
                role=ChatMessageRole.USER if index % 2 == 0 else ChatMessageRole.ASSISTANT,
                content=f"Message {index}",
            )
            for index in range(52)
        ]
    )
    await db_session.commit()
    ask = AsyncMock(wraps=fake_ai.ask_workspace)
    monkeypatch.setattr(fake_ai, "ask_workspace", ask)

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/chat",
        headers={"Authorization": f"Bearer {create_access_token(owner.id)}"},
        json={"question": "Latest?"},
    )
    assert response.status_code == 200
    ask.assert_awaited_once_with(
        workspace_id=workspace.id,
        query="Latest?",
        chat_history=[
            ChatMessage(
                role=ChatRole.USER if index % 2 == 0 else ChatRole.ASSISTANT,
                content=f"Message {index}",
            )
            for index in range(2, 52)
        ],
    )


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_chat_history_rolls_back_when_commit_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    owner = await create_user(db_session)
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    chat_path = f"/api/v1/workspaces/{workspace.id}/chat"
    history_path = f"{chat_path}/history"
    created = await client.post(chat_path, headers=headers, json={"question": "First question"})
    assert created.status_code == 200
    before = await client.get(history_path, headers=headers)
    assert before.status_code == 200
    assert len(before.json()) == 2

    async def fail_commit() -> None:
        # Let the SQL run inside the transaction before simulating a commit failure.
        await db_session.flush()
        raise SQLAlchemyError("Simulated commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url=client.base_url) as error_client:
            if method == "POST":
                failed = await error_client.post(
                    chat_path, headers=headers, json={"question": "Second question"}
                )
            else:
                failed = await error_client.delete(history_path, headers=headers)

    assert failed.status_code == 500
    assert failed.json() == {"detail": "Internal server error"}
    after = await client.get(history_path, headers=headers)
    assert after.status_code == 200
    assert after.json() == before.json()
