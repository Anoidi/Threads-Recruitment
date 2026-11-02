"""Audit I-4/D5: every AI gateway call must be accounted for.

A recruiter experimenting with writers needs to know which model answered,
how many tokens a route burns and how long the answer took. Until now the
gateway was a black box: failures and costs were invisible.
"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.ai as ai_module
from app.ai import AIService
from app.config import settings
from app.enums import ContentType
from app.models import AIUsage


class SameSession:
    """Travel through the monkeypatched SessionLocal back to the test db."""

    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


def _patch_gateway(monkeypatch, body: dict):
    response = httpx.Response(
        200,
        content=json.dumps(body).encode("utf-8"),
        request=httpx.Request("POST", "http://gateway-test"),
    )

    async def fake_post(self, url, json=None, headers=None):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


@pytest.mark.asyncio
async def test_chat_records_gateway_usage(db, monkeypatch):
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    monkeypatch.setattr(settings, "litellm_base_url", "http://gateway-test")
    monkeypatch.setattr(settings, "ai_retries", 0)
    _patch_gateway(monkeypatch, {
        "model": "omniroute/qwen-test",
        "choices": [{"message": {"content": "Привіт, у мене є вакансія"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    })
    monkeypatch.setattr(ai_module, "SessionLocal", lambda: SameSession(db))

    content, finish = await AIService()._chat("free-default", "test", 100, json_mode=True)

    assert content == "Привіт, у мене є вакансія"
    assert finish == "stop"
    rows = list((await db.execute(select(AIUsage))).scalars())
    assert len(rows) == 1
    row = rows[0]
    assert row.route == "free-default"
    assert row.resolved_model == "omniroute/qwen-test"
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (120, 40, 160)
    assert row.status == "ok"
    assert row.finish_reason == "stop"
    assert row.error == ""
    assert row.duration_ms >= 0
    assert row.created_at is not None
    int(row.duration_ms)


@pytest.mark.asyncio
async def test_failed_gateway_call_is_recorded_as_error(db, monkeypatch):
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    monkeypatch.setattr(settings, "litellm_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "ai_retries", 0)
    monkeypatch.setattr(ai_module, "SessionLocal", lambda: SameSession(db))

    with pytest.raises(HTTPException):
        await AIService()._chat("free-fast", "test", 10)

    rows = list((await db.execute(select(AIUsage))).scalars())
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "error"
    assert row.route == "free-fast"
    assert row.resolved_model == ""
    assert "gateway" in row.error.lower()
    assert row.prompt_tokens == 0 and row.total_tokens == 0


@pytest.mark.asyncio
async def test_mock_ai_writes_no_usage_rows(db, monkeypatch):
    """Deterministic mock answers never reach the gateway: no rows, no DB use.

    The SessionLocal stub raises if anything tries to open one, so the test
    would fail loudly on a silent write attempt.
    """
    monkeypatch.setattr(settings, "ai_mock_mode", True)

    def _must_not_open():
        raise AssertionError("mock AI must not write ai_usage rows")

    monkeypatch.setattr(ai_module, "SessionLocal", _must_not_open)

    await AIService().generate_post(None, ContentType.EDUCATIONAL, "uk", "professional", None, "Як перевірити умови працевлаштування")

    assert (await db.execute(select(func.count(AIUsage.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_agents_usage_endpoint_aggregates_per_route(db, client):
    db.add_all([
        AIUsage(route="free-default", resolved_model="m-a", prompt_tokens=100, completion_tokens=50,
                total_tokens=150, duration_ms=1200, finish_reason="stop", status="ok",
                created_at=datetime.now(timezone.utc)),
        AIUsage(route="free-default", resolved_model="m-a", prompt_tokens=200, completion_tokens=100,
                total_tokens=300, duration_ms=800, finish_reason="stop", status="ok",
                created_at=datetime.now(timezone.utc)),
        AIUsage(route="free-fast", resolved_model="m-b", prompt_tokens=10, completion_tokens=5,
                total_tokens=15, duration_ms=300, finish_reason="stop", status="error",
                error="AI gateway error 500: oops", created_at=datetime.now(timezone.utc)),
    ])
    await db.commit()

    response = await client.get("/api/threads/agents/usage")
    assert response.status_code == 200
    data = response.json()
    totals = {item["route"]: item for item in data["totals_by_route"]}
    assert set(totals) == {"free-default", "free-fast"}
    assert totals["free-default"]["calls"] == 2
    assert totals["free-default"]["total_tokens"] == 450
    assert totals["free-default"]["errors"] == 0
    assert totals["free-fast"]["calls"] == 1
    assert totals["free-fast"]["errors"] == 1
    assert len(data["recent"]) == 3
    newest = data["recent"][0]
    assert newest["route"] == "free-fast"
    assert newest["resolved_model"] == "m-b"
