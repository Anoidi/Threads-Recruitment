"""The agent list must survive a role that has no model route.

A new role was added to the enum without a matching entry in MODEL_ROUTE, and
the agent list answered 500. The page then showed nothing at all, because the
frontend also assumed every role it receives is described in its own table.
Both halves are pinned down here.
"""
import pytest
from sqlalchemy import select

from app.enums import AgentRole
from app.models import AgentConfig


@pytest.mark.asyncio
async def test_a_role_without_a_model_route_still_gets_a_working_one(client, db, monkeypatch):
    from app import ai

    monkeypatch.delitem(ai.MODEL_ROUTE, AgentRole.REPLIER, raising=False)
    existing = (await db.execute(
        select(AgentConfig).where(AgentConfig.role == AgentRole.REPLIER)
    )).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()

    response = await client.get("/api/threads/agents")
    assert response.status_code == 200
    roles = {item["role"] for item in response.json()}
    assert AgentRole.REPLIER.value in roles
    replier = next(item for item in response.json() if item["role"] == AgentRole.REPLIER.value)
    assert replier["model_alias"], "a role without a route still needs a working one"


@pytest.mark.asyncio
async def test_every_role_has_a_model_route(client):
    from app.ai import MODEL_ROUTE

    response = await client.get("/api/threads/agents")
    assert response.status_code == 200
    for role in AgentRole:
        assert role in MODEL_ROUTE, f"{role} has no model route"
        assert role.value in {item["role"] for item in response.json()}
