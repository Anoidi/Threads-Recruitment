from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.ai import AIService
from app.enums import AgentRole, PostStatus
from app.models import AgentRun, PostMetric, PostRevision, ThreadsPost
from app.threads_client import ThreadsAPIError
from worker import main as worker_main


@pytest.mark.asyncio
async def test_writer_creates_draft_and_revision_when_local_ai_times_out(db, monkeypatch):
    calls = 0
    schedule_calls = []
    async def schedule(_db, post):
        schedule_calls.append(post.id)
        return True
    monkeypatch.setattr(worker_main, '_auto_schedule_post', schedule)

    async def timeout(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise HTTPException(504, "local model timed out")

    monkeypatch.setattr(AIService, "plan_content", timeout)
    run = AgentRun(role=AgentRole.WRITER, status="QUEUED")
    db.add(run)
    await db.commit()

    await worker_main.run_agent(db, run.id, AgentRole.WRITER)

    await db.refresh(run)
    post = (await db.execute(select(ThreadsPost))).scalar_one()
    revision = (await db.execute(select(PostRevision).where(PostRevision.post_id == post.id))).scalar_one()
    assert calls == 1
    assert schedule_calls == []
    assert run.status == "COMPLETED"
    assert post.status == PostStatus.AI_GENERATED
    assert post.content.strip()
    assert revision.content == post.content
    assert revision.editor == "writer-agent"
    assert "резерв" in (run.result_summary or "").lower()


@pytest.mark.asyncio
async def test_analyst_returns_actionable_no_data_summary(db):
    run = AgentRun(role=AgentRole.ANALYST, status="QUEUED")
    db.add(run)
    await db.commit()

    await worker_main.run_agent(db, run.id, AgentRole.ANALYST)

    await db.refresh(run)
    summary = run.result_summary or ""
    assert run.status == "COMPLETED"
    assert "нет публикаций с метриками" in summary.lower()
    assert "следующий шаг" in summary.lower()


@pytest.mark.asyncio
async def test_analyst_does_not_claim_winner_from_two_young_posts(db):
    educational = ThreadsPost(
        content="Практический чек-лист документов для работы в Польше.",
        content_type="EDUCATIONAL",
        language="ru",
        status=PostStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
        remote_id="remote-educational",
    )
    vacancy = ThreadsPost(
        content="Вакансия на складе с проверенными условиями.",
        content_type="VACANCY",
        language="ru",
        status=PostStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
        remote_id="remote-vacancy",
    )
    db.add_all([educational, vacancy])
    await db.flush()
    db.add_all([
        PostMetric(post_id=educational.id, views=200, likes=30, replies=12, reposts=8, quotes=2),
        PostMetric(post_id=vacancy.id, views=300, likes=5, replies=1, reposts=0, quotes=0),
    ])
    run = AgentRun(role=AgentRole.ANALYST, status="QUEUED")
    db.add(run)
    await db.commit()

    await worker_main.run_agent(db, run.id, AgentRole.ANALYST)

    await db.refresh(run)
    summary = run.result_summary or ""
    assert 'недостаточно сопоставимых данных' in summary.lower()
    assert 'лучший сегмент' not in summary.lower()
    assert 'сопоставимых: 0' in summary.lower()


@pytest.mark.asyncio
async def test_agent_run_becomes_failed_when_agent_raises(db, monkeypatch):
    async def fail(_db):
        raise RuntimeError("community exploded")

    monkeypatch.setattr(worker_main, "sync_community", fail)
    run = AgentRun(role=AgentRole.COMMUNITY, status="QUEUED")
    db.add(run)
    await db.commit()

    with pytest.raises(RuntimeError, match="community exploded"):
        await worker_main.run_agent(db, run.id, AgentRole.COMMUNITY)

    await db.refresh(run)
    assert run.status == "FAILED"
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert "community exploded" in (run.error or "")


@pytest.mark.asyncio
async def test_community_agent_is_paused_when_meta_blocks_api(db, monkeypatch):
    async def blocked(_db):
        raise ThreadsAPIError("Meta blocked API access for this app", category="app_blocked", graph_code=200, fbtrace_id="TRACE-PAUSED")

    monkeypatch.setattr(worker_main, "sync_community", blocked)
    run = AgentRun(role=AgentRole.COMMUNITY, status="QUEUED")
    db.add(run)
    await db.commit()

    await worker_main.run_agent(db, run.id, AgentRole.COMMUNITY)

    await db.refresh(run)
    assert run.status == "BLOCKED"
    assert run.finished_at is not None
    assert "Meta" in (run.result_summary or "")
    assert "TRACE-PAUSED" in (run.error or "")


@pytest.mark.asyncio
async def test_content_plan_timeout_uses_deterministic_fallback_once(db, monkeypatch):
    calls = 0

    async def timeout(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise HTTPException(504, "local model timed out")

    monkeypatch.setattr(AIService, "plan_content", timeout)

    summary = await worker_main.content_plan(db, {"count": 3, "language": "uk"})

    posts = list((await db.execute(select(ThreadsPost))).scalars())
    assert calls == 1
    assert len(posts) == 3
    assert all(post.status == PostStatus.AI_GENERATED for post in posts)
    assert "резерв" in summary.lower()
