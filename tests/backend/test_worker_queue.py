"""Regression tests for the worker queue and the strategist route.

Two operational defects motivated these tests. A manual agent run occupies
the only worker for a minute or more, and publication jobs sit in the same
queue - so clicking "Run" could delay a scheduled post. And the strategist
went through Hermes, an orchestration runtime in front of the same small
local model, which turned every call into a timeout while the direct route
answers in well under a minute.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import worker.main as worker_main
from app.config import settings
from app.enums import JobStatus
from app.models import BackgroundJob
from worker.main import claim_job, strategize


@pytest.mark.asyncio
async def test_publication_is_claimed_before_a_slow_agent_job(db, monkeypatch):
    monkeypatch.setattr(settings, "worker_job_kinds", "")
    now = datetime.now(timezone.utc)
    agent_job = BackgroundJob(
        kind="run_agent", payload={"run_id": 1}, idempotency_key="q-agent",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=5),
    )
    publish_job = BackgroundJob(
        kind="publish_post", payload={"post_id": 1}, idempotency_key="q-publish",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=1),
    )
    db.add_all([agent_job, publish_job])
    await db.commit()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    job_id = await claim_job()
    assert job_id == publish_job.id


@pytest.mark.asyncio
async def test_a_dedicated_agent_worker_only_takes_agent_jobs(db, monkeypatch):
    monkeypatch.setattr(settings, "worker_job_kinds", "run_agent")
    now = datetime.now(timezone.utc)
    publish_job = BackgroundJob(
        kind="publish_post", payload={"post_id": 1}, idempotency_key="q-publish-2",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=1),
    )
    agent_job = BackgroundJob(
        kind="run_agent", payload={"run_id": 2}, idempotency_key="q-agent-2",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=5),
    )
    db.add_all([publish_job, agent_job])
    await db.commit()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    job_id = await claim_job()
    assert job_id == agent_job.id


@pytest.mark.asyncio
async def test_primary_worker_never_claims_agent_kinds(db, monkeypatch):
    """Worker split (audit I-2/D2): the primary worker does not claim the
    slow model-backed agent jobs; worker-agents owns them."""
    monkeypatch.setattr(settings, "worker_job_kinds", "")
    monkeypatch.setattr(settings, "worker_role", "primary")
    now = datetime.now(timezone.utc)
    plan_job = BackgroundJob(
        kind="content_plan", payload={}, idempotency_key="q-split-1",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=5),
    )
    agent_job = BackgroundJob(
        kind="run_agent", payload={"run_id": 3}, idempotency_key="q-split-2",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=5),
    )
    db.add_all([plan_job, agent_job])
    await db.commit()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    assert await claim_job() is None


@pytest.mark.asyncio
async def test_primary_worker_still_claims_non_agent_kinds(db, monkeypatch):
    monkeypatch.setattr(settings, "worker_job_kinds", "")
    monkeypatch.setattr(settings, "worker_role", "primary")
    now = datetime.now(timezone.utc)
    sync_job = BackgroundJob(
        kind="sync_community", payload={}, idempotency_key="q-split-3",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=2),
    )
    db.add(sync_job)
    await db.commit()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    assert await claim_job() == sync_job.id


@pytest.mark.asyncio
async def test_agent_worker_claims_content_plan(db, monkeypatch):
    monkeypatch.setattr(settings, "worker_job_kinds", "")
    monkeypatch.setattr(settings, "worker_role", "agents")
    now = datetime.now(timezone.utc)
    plan_job = BackgroundJob(
        kind="content_plan", payload={}, idempotency_key="q-split-4",
        status=JobStatus.PENDING, run_at=now - timedelta(minutes=5),
    )
    db.add(plan_job)
    await db.commit()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    assert await claim_job() == plan_job.id


@pytest.mark.asyncio
async def test_strategist_uses_the_direct_model_route_by_default(db, monkeypatch):
    """Hermes adds an agent session in front of the same local model.

    With a 4B model that session consumed the whole request budget and every
    call timed out, so the strategist now asks the configured route directly.
    """
    from app.models import Vacancy

    monkeypatch.setattr(settings, "ai_mock_mode", False)
    monkeypatch.setattr(settings, "hermes_for_strategist", False)
    vacancy = Vacancy(
        title="Склад", description="Робота на складі.", location="Kraków",
        salary="30.00 gross/hour", currency="PLN", schedule="2 shifts", language="uk", active=True,
    )
    db.add(vacancy)
    await db.commit()

    asked: list[str] = []

    class FakeAIService:
        async def strategy_proposal(self, task: str):
            asked.append(task)
            return {"title": "Уточніть умови", "explanation": "Питання про умови.", "language": "uk", "vacancy_id": vacancy.id}

    def hermes_must_not_run(*args, **kwargs):
        raise AssertionError("Hermes must not be used unless it is explicitly enabled")

    monkeypatch.setattr(worker_main, "AIService", lambda: FakeAIService())
    monkeypatch.setattr(worker_main.hermes_client, "ask", hermes_must_not_run)

    summary = await strategize(db, [vacancy])

    assert asked, "the strategist model was not called"
    assert "Strategy angle proposed" in summary
    from app.models import Topic
    topics = list((await db.execute(select(Topic))).scalars())
    assert [topic.source for topic in topics] == ["strategist"]


@pytest.mark.asyncio
async def test_strategist_falls_back_without_failing(db, monkeypatch):
    from app.models import Topic, Vacancy

    monkeypatch.setattr(settings, "ai_mock_mode", False)
    monkeypatch.setattr(settings, "hermes_for_strategist", False)
    vacancy = Vacancy(
        title="Склад", description="Робота на складі.", location="Kraków",
        salary="30.00 gross/hour", currency="PLN", language="uk", active=True,
    )
    db.add(vacancy)
    await db.commit()

    class BrokenService:
        async def strategy_proposal(self, task: str):
            raise RuntimeError("model is unreachable")

    monkeypatch.setattr(worker_main, "AIService", lambda: BrokenService())

    summary = await strategize(db, [vacancy])

    assert "Model fallback" in summary
    topics = list((await db.execute(select(Topic))).scalars())
    assert len(topics) == 1
