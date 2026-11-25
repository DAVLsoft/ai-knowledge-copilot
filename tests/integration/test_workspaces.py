from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.workspace import Workspace
from app.services.ai import LLMTimeoutError, StubAIService
from tests.integration.factories import create_user


async def test_create_workspace_and_list_only_own_workspaces(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await create_user(db_session)
    other = await create_user(db_session, email="other@example.com", username="other")
    owner_headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}

    response = await client.post(
        "/api/v1/workspaces", headers=owner_headers, json={"name": "  Engineering  "}
    )
    assert response.status_code == 201
    workspace = response.json()
    assert workspace["name"] == "Engineering"
    assert workspace["owner_id"] == str(owner.id)

    own_list = await client.get("/api/v1/workspaces", headers=owner_headers)
    assert own_list.status_code == 200
    assert own_list.json() == [workspace]

    other_list = await client.get("/api/v1/workspaces", headers=other_headers)
    assert other_list.status_code == 200
    assert other_list.json() == []


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_workspace_endpoints_require_authentication(client: AsyncClient, method: str) -> None:
    response = await client.request(method, "/api/v1/workspaces", json={"name": "Engineering"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("payload", [{"name": "   "}, {"name": "Docs", "owner_id": "ignored"}])
async def test_create_workspace_rejects_invalid_body(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, str]
) -> None:
    owner = await create_user(db_session)
    response = await client.post(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {create_access_token(owner.id)}"},
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid request"


async def test_chat_switches_between_owned_workspaces_and_rejects_foreign_workspace(
    client: AsyncClient, db_session: AsyncSession, fake_ai: StubAIService, tmp_path: Path
) -> None:
    owner = await create_user(db_session)
    other = await create_user(db_session, email="other@example.com", username="other")
    first = Workspace(name="Engineering", owner_id=owner.id)
    second = Workspace(name="HR", owner_id=owner.id)
    foreign = Workspace(name="Private", owner_id=other.id)
    db_session.add_all([first, second, foreign])
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    source_id = uuid4()
    source = tmp_path / "guide.txt"
    source_text = "The guide is on the intranet."
    source.write_text(source_text, encoding="utf-8")
    await fake_ai.ingest_document(
        document_id=source_id, workspace_id=first.id, file_path=source, filename=source.name
    )

    first_response = await client.post(
        f"/api/v1/workspaces/{first.id}/chat", headers=headers, json={"question": "  guide  "}
    )
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert source.name in first_body["answer"]
    assert first_body["citations"] == [
        {
            "document_id": str(source_id),
            "filename": source.name,
            "chunk_index": 0,
            "page_number": None,
            "snippet": source_text,
            "score": 1.0,
        }
    ]

    second_response = await client.post(
        f"/api/v1/workspaces/{second.id}/chat", headers=headers, json={"question": "guide"}
    )
    assert second_response.status_code == 200
    assert second_response.json()["citations"] == []

    for workspace_id in (foreign.id, uuid4()):
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/chat", headers=headers, json={"question": "Guide?"}
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found"}

    assert fake_ai.queries == [(first.id, "guide"), (second.id, "guide")]


async def test_chat_rejects_unauthenticated_and_invalid_requests(
    client: AsyncClient, db_session: AsyncSession, fake_ai: StubAIService
) -> None:
    owner = await create_user(db_session)
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    path = f"/api/v1/workspaces/{uuid4()}/chat"
    unauthenticated = await client.post(path, json={"question": "Guide?"})
    assert unauthenticated.status_code == 401
    invalid = await client.post(path, headers=headers, json={"question": "   "})
    assert invalid.status_code == 422
    assert fake_ai.queries == []


async def test_owned_workspace_chat_returns_504_when_ai_times_out(
    client: AsyncClient, db_session: AsyncSession, fake_ai: StubAIService
) -> None:
    owner = await create_user(db_session)
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.commit()
    fake_ai.failure = LLMTimeoutError()

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/chat",
        headers={"Authorization": f"Bearer {create_access_token(owner.id)}"},
        json={"question": "Guide?"},
    )
    assert response.status_code == 504
    assert response.json() == {"detail": "The AI model did not respond in time"}
