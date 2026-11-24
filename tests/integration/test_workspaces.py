from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_rag_service
from app.core.security import create_access_token
from app.main import app
from app.models.workspace import Workspace
from app.services.rag import UnconfiguredRAGService
from tests.fakes import FakeRAGService
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
    client: AsyncClient, db_session: AsyncSession, fake_rag: FakeRAGService
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
    await fake_rag.index_document(source_id, "Example document")

    for workspace in (first, second):
        response = await client.post(
            f"/api/v1/workspaces/{workspace.id}/chat",
            headers=headers,
            json={"question": "  Where is the guide?  "},
        )
        assert response.status_code == 200
        assert response.json() == {
            "answer": fake_rag.canned_answer,
            "source_document_ids": [str(source_id)],
        }

    for workspace_id in (foreign.id, uuid4()):
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/chat", headers=headers, json={"question": "Guide?"}
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found"}

    assert fake_rag.questions == [
        (first.id, "Where is the guide?"),
        (second.id, "Where is the guide?"),
    ]


async def test_chat_rejects_unauthenticated_and_invalid_requests(
    client: AsyncClient, db_session: AsyncSession, fake_rag: FakeRAGService
) -> None:
    owner = await create_user(db_session)
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    path = f"/api/v1/workspaces/{uuid4()}/chat"
    unauthenticated = await client.post(path, json={"question": "Guide?"})
    assert unauthenticated.status_code == 401
    invalid = await client.post(path, headers=headers, json={"question": "   "})
    assert invalid.status_code == 422
    assert fake_rag.questions == []


async def test_owned_workspace_chat_returns_503_without_a_configured_rag(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await create_user(db_session)
    workspace = Workspace(name="Engineering", owner_id=owner.id)
    db_session.add(workspace)
    await db_session.commit()
    monkeypatch.setitem(app.dependency_overrides, get_rag_service, UnconfiguredRAGService)

    response = await client.post(
        f"/api/v1/workspaces/{workspace.id}/chat",
        headers={"Authorization": f"Bearer {create_access_token(owner.id)}"},
        json={"question": "Guide?"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Chat service is unavailable"}
