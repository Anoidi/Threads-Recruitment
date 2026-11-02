import pytest
from sqlalchemy import func, select

from app.enums import PostStatus
from app.models import AppSetting, BackgroundJob, ThreadsPost, Topic
from worker.main import content_plan, schedule_periodic


@pytest.mark.asyncio
async def test_same_text_with_different_hook_is_not_recreated(db, monkeypatch):
    from app.ai import AIService
    original = ThreadsPost(content='Уточніть графік роботи.', hook='Старий заголовок', content_type='FAQ', language='uk')
    db.add(original)
    await db.flush()
    async def plan(self, **kwargs):
        return [dict(content='Уточніть   графік роботи.', hook='Новий заголовок', content_type='FAQ')]
    monkeypatch.setattr(AIService, 'plan_content', plan)
    result = await content_plan(db, {'count': 1})
    assert (await db.execute(select(func.count(ThreadsPost.id)))).scalar_one() == 1
    assert 'duplicate' in result


@pytest.mark.asyncio
async def test_same_hook_does_not_block_changed_conditions(db, monkeypatch):
    from app.ai import AIService
    db.add(ThreadsPost(content='Ставка 23 зл/год.', hook='Оплата', content_type='FAQ', language='uk'))
    await db.flush()
    async def plan(self, **kwargs):
        return [dict(content='Ставка 25 зл/год.', hook='Оплата', content_type='FAQ')]
    monkeypatch.setattr(AIService, 'plan_content', plan)
    await content_plan(db, {'count': 1})
    assert (await db.execute(select(func.count(ThreadsPost.id)))).scalar_one() == 2


@pytest.mark.asyncio
async def test_unrelated_plan_does_not_consume_first_research_topic(db, monkeypatch):
    from app.ai import AIService
    topic = Topic(title='Житло', source='threads-keyword-search', priority=90)
    db.add(topic)
    await db.flush()
    async def plan(self, **kwargs):
        return [dict(content='Уточніть графік роботи.', hook='Графік', content_type='FAQ')]
    monkeypatch.setattr(AIService, 'plan_content', plan)
    await content_plan(db, {'count': 1})
    await db.refresh(topic)
    assert topic.status == 'DISCOVERED'


@pytest.mark.asyncio
async def test_legal_draft_is_not_scheduled_even_with_auto_post_enabled(db, monkeypatch):
    from types import SimpleNamespace
    from worker import main as worker
    from app.config import settings
    from tests.backend.conftest import set_autonomy_mode
    monkeypatch.setattr(settings, 'threads_mock_mode', False)
    await set_autonomy_mode(db, "SEMI_AUTO")
    async def account(_): return SimpleNamespace(access_token_encrypted='present')
    monkeypatch.setattr(worker, '_connected_account', account)
    post = ThreadsPost(content='Перед оформленням перевірте документи та актуальні правила. ' * 3,
        content_type='LEGAL_INFORMATION', language='uk', status=PostStatus.AI_GENERATED)
    db.add(post)
    await db.flush()
    assert not await worker._auto_schedule_post(db, post)
    assert post.status == PostStatus.AI_GENERATED
    assert 'проверка оператором' in post.reasoning_summary
    assert (await db.execute(select(BackgroundJob))).scalars().first() is None


@pytest.mark.asyncio
async def test_oversized_vacancy_does_not_abort_other_plan_ideas(db, monkeypatch):
    from app.ai import AIService
    from app.models import Vacancy
    vacancy = Vacancy(title='Склад', description='Робота на складі', location='Kraków', salary='23 зл/год',
        housing='Житло з умовами ' * 50, language='uk', active=True)
    db.add(vacancy)
    await db.flush()
    async def plan(self, **kwargs):
        return [dict(content='Vacancy idea', hook='Vacancy', content_type='VACANCY', vacancy_id=vacancy.id),
                dict(content='Уточніть графік перед виходом на роботу.', hook='Графік', content_type='FAQ')]
    monkeypatch.setattr(AIService, 'plan_content', plan)
    summary = await content_plan(db, {'count': 2, 'language': 'uk'})
    posts = list((await db.execute(select(ThreadsPost))).scalars())
    assert len(posts) == 1
    assert posts[0].content_type == 'FAQ'
    assert 'Требуется разделить' in summary
    assert str(vacancy.id) in summary


class TestContentPlanEndpoint:
    @pytest.mark.asyncio
    async def test_queues_plan_job(self, client, db):
        response = await client.post("/api/threads/content-plan")
        assert response.status_code == 202
        payload = response.json()
        assert payload["job_id"] > 0
        assert payload["language"] == "uk"
        job = (await db.execute(select(BackgroundJob).where(BackgroundJob.id == payload["job_id"]))).scalar_one()
        assert job.kind == "content_plan"
        assert job.payload["count"] == 3

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_count(self, client):
        response = await client.post("/api/threads/content-plan?count=99")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_same_minute_requests_are_deduplicated(self, client, db):
        await client.post("/api/threads/content-plan")
        await client.post("/api/threads/content-plan")
        total = (await db.execute(select(func.count(BackgroundJob.id)).where(BackgroundJob.kind == "content_plan"))).scalar_one()
        assert total == 1


class TestContentPlanJob:
    @pytest.mark.asyncio
    async def test_creates_audience_drafts(self, db):
        summary = await content_plan(db, {"count": 3, "language": "uk"})
        posts = list((await db.execute(select(ThreadsPost))).scalars())
        assert len(posts) == 3
        assert all(post.status == PostStatus.AI_GENERATED for post in posts)
        assert all(post.content.strip() and len(post.content) <= 500 for post in posts)
        # The audience plan must not be vacancy-only.
        types = {post.content_type.value for post in posts}
        assert types != {"VACANCY"}
        assert summary.startswith("Planned 3")

    @pytest.mark.asyncio
    async def test_skips_duplicate_hooks(self, db):
        await content_plan(db, {"count": 3, "language": "uk"})
        await content_plan(db, {"count": 3, "language": "uk"})
        total = (await db.execute(select(func.count(ThreadsPost.id)))).scalar_one()
        assert total == 3

    @pytest.mark.asyncio
    async def test_uses_ranked_trend_as_original_content_angle(self, db):
        topic = Topic(title="Житло для працівників у Кракові", source="threads-keyword-search", priority=90)
        db.add(topic)
        await db.commit()
        await content_plan(db, {"count": 3, "language": "uk"})
        posts = list((await db.execute(select(ThreadsPost))).scalars())
        assert any(post.content_type == "TREND_RESPONSE" for post in posts)
        await db.refresh(topic)
        assert topic.status == "USED"


class TestPeriodicScheduler:
    @pytest.mark.asyncio
    async def test_first_tick_queues_monitoring_and_plan(self, db):
        queued = await schedule_periodic(db)
        assert "sync_community" in queued
        assert "content_plan" in queued
        # One strategy angle per day, so the planner always has a fresh angle
        # to work with.
        assert "run_agent:STRATEGIST" in queued
        jobs = list((await db.execute(select(BackgroundJob))).scalars())
        kinds = {job.kind for job in jobs}
        assert kinds == {"sync_community", "content_plan", "scout_sources", "supervisor_scan", "quality_guard_scan", "run_agent", "retention", "prepare_content"}
        # Recurring keys must be stable so restarts do not pile up duplicates.
        assert any(job.idempotency_key == "content-plan:auto" or job.idempotency_key.endswith(":auto") for job in jobs)

    @pytest.mark.asyncio
    async def test_second_tick_within_window_is_idempotent(self, db):
        await schedule_periodic(db)
        queued = await schedule_periodic(db)
        assert queued == []

    @pytest.mark.asyncio
    async def test_manual_plan_today_skips_auto_plan(self, db):
        from datetime import datetime, timezone

        # A manual run already produced content today -> the scheduler must
        # skip the automatic plan without failing, even with two matching rows.
        db.add(BackgroundJob(kind="content_plan", payload={"count": 2, "language": "uk"}, idempotency_key=f"content-plan:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"))
        await db.commit()
        db.add(BackgroundJob(kind="content_plan", payload={"count": 3, "language": "uk"}, idempotency_key=f"content-plan:{datetime.now(timezone.utc).strftime('%Y%m%d')}:auto"))
        await db.commit()
        queued = await schedule_periodic(db)
        assert "content_plan" not in queued
        assert "sync_community" in queued

    @pytest.mark.asyncio
    async def test_disabled_automation_queues_nothing(self, db):
        db.add(AppSetting(key="auto_community_sync", value=False))
        db.add(AppSetting(key="auto_content_plan", value=False))
        db.add(AppSetting(key="auto_strategy_angle", value=False))
        await db.commit()
        queued = await schedule_periodic(db)
        # Source scouting is intentionally independent of the community inbox
        # and content-plan toggles; it only stores sourced drafts/topics.
        # The weekly retention sweep follows its own cadence and is a dry run
        # by default (audit I-7: deletion is an explicit owner decision).
        assert queued == ["prepare_content", "supervisor_scan", "quality_guard_scan", "scout_sources", "retention"]
