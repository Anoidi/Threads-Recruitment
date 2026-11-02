from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json

import pytest
from sqlalchemy import select

from app.ai import AIService
from app.enums import JobStatus, PostStatus
from app.models import AppSetting, BackgroundJob, PostRevision, ThreadsPost, Vacancy
from app.model_qa import QaResult, prepare_post_qa, qa_is_current
from app.content_stock import stock_report, upcoming_slots
from tests.backend.conftest import set_autonomy_mode

TEXT = "Перед поїздкою складіть список запитань до рекрутера. Уточніть умови та попросіть пояснити незрозумілі пункти, щоб ухвалити зважене рішення про роботу. Збережіть відповіді та спокійно порівняйте пропозиції перед остаточним рішенням."
REPAIRED = "Не поспішайте погоджуватися на роботу. Запишіть запитання про умови та обговоріть їх із рекрутером. Так буде простіше порівняти пропозиції й вирішити, що підходить саме вам."


async def draft(db, **kwargs):
    post = ThreadsPost(content=TEXT, content_type="EDUCATIONAL", language="uk", status=PostStatus.AI_GENERATED, **kwargs)
    db.add(post)
    await db.flush()
    return post


@pytest.mark.asyncio
async def test_failed_draft_is_repaired_and_rechecked_before_queue(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    post = await draft(db)
    reviews = []
    async def review(text, facts, **kw):
        reviews.append(text)
        return QaResult(text == REPAIRED, () if text == REPAIRED else ("MACHINE_TONE",), "Природніше")
    async def rewrite(self, route, prompt, *args, **kw):
        assert "feedback" in prompt and "confirmed facts" in prompt
        return json.dumps({"content": REPAIRED}), "stop"
    monkeypatch.setattr("app.model_qa.review_post", review)
    monkeypatch.setattr(AIService, "_chat", rewrite)
    assert await prepare_post_qa(db, post, repair=True)
    assert reviews == [TEXT, REPAIRED]
    assert post.version == 2 and post.qa_repairs == 1 and post.qa_attempts == 2
    assert post.status == PostStatus.AI_GENERATED
    assert await qa_is_current(db, post)
    assert (await db.execute(select(PostRevision).where(PostRevision.post_id == post.id))).scalar_one().content == REPAIRED


@pytest.mark.asyncio
async def test_two_repairs_are_a_durable_limit(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    post = await draft(db)
    calls = []
    async def review(*args, **kw):
        return QaResult(False, ("MACHINE_TONE",), "Переписати")
    async def rewrite(*args, **kw):
        calls.append(1)
        return json.dumps({"content": REPAIRED + "!" * len(calls)}), "stop"
    monkeypatch.setattr("app.model_qa.review_post", review)
    monkeypatch.setattr(AIService, "_chat", rewrite)
    assert not await prepare_post_qa(db, post, repair=True)
    await db.commit()
    assert not await prepare_post_qa(db, post, repair=True)
    assert post.qa_repairs == 2 and post.qa_attempts == 3 and len(calls) == 2
    assert post.model_qa_passed is False and post.failure_reason
    assert not list((await db.execute(select(BackgroundJob))).scalars())


@pytest.mark.asyncio
async def test_changed_contact_or_facts_invalidates_review_without_model_call(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    vacancy = Vacancy(title="Склад", description="Комплектація", location="Kraków", salary="30 PLN/hour", source_confirmed_at=datetime.now(timezone.utc))
    db.add(vacancy)
    await db.flush()
    post = await draft(db, vacancy_id=vacancy.id)
    async def review(*args, **kw):
        return QaResult(True)
    monkeypatch.setattr("app.model_qa.review_post", review)
    assert await prepare_post_qa(db, post)
    assert await qa_is_current(db, post)
    vacancy.salary = "35 PLN/hour"
    await db.flush()
    assert not await qa_is_current(db, post)
    assert await prepare_post_qa(db, post)
    db.add(AppSetting(key="phone_contact", value="+48 111 222 333"))
    await db.flush()
    assert not await qa_is_current(db, post)
    assert await prepare_post_qa(db, post)
    post.language = "pl"
    assert not await qa_is_current(db, post)


@pytest.mark.asyncio
async def test_unconfirmed_source_does_not_trigger_rewrite(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    vacancy = Vacancy(title="Склад", description="Робота", location="Kraków")
    db.add(vacancy)
    await db.flush()
    post = await draft(db, vacancy_id=vacancy.id)
    async def forbidden(*args, **kw):
        raise AssertionError("An unverified source needs a person, not a rewrite")
    monkeypatch.setattr("app.model_qa.review_post", forbidden)
    monkeypatch.setattr(AIService, "_chat", forbidden)
    assert not await prepare_post_qa(db, post, repair=True)
    assert post.qa_repairs == 0
    assert "confirmation" in post.failure_reason


def test_stock_slots_follow_warsaw_clock_across_dst():
    zone = ZoneInfo("Europe/Warsaw")
    now = datetime(2026, 10, 24, 6, tzinfo=timezone.utc)
    slots = upcoming_slots(now, 3, zone)
    assert all(s.astimezone(zone).hour in {9, 13, 18} for s in slots)
    assert len(slots) == len(set(slots)) == 9
    assert slots[0].hour == 7 and slots[3].hour == 8


@pytest.mark.asyncio
async def test_stock_excludes_stale_queue_and_reports_gaps(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    now = datetime.now(timezone.utc)
    post = await draft(db)
    async def review(*args, **kw):
        return QaResult(True)
    monkeypatch.setattr("app.model_qa.review_post", review)
    assert await prepare_post_qa(db, post)
    report = await stock_report(db, now)
    assert report["ready_reserve"] == 1
    post.status = PostStatus.SCHEDULED
    post.scheduled_at = report["missing_slots"][0]
    await db.flush()
    report = await stock_report(db, now)
    assert report["ready_scheduled"] == 1 and report["covered_slots"] == 1
    post.content += " Інший текст."
    await db.flush()
    report = await stock_report(db, now)
    assert report["ready_scheduled"] == 0 and report["stale_scheduled_ids"] == [post.id]


@pytest.mark.asyncio
async def test_replenisher_fills_slot_and_enqueues_one_bounded_generation_batch(db, monkeypatch):
    from worker.content_pipeline import replenish_content
    await set_autonomy_mode(db, "AUTO")
    post = await draft(db)
    scheduled = []
    async def review(*args, **kw):
        return QaResult(True)
    monkeypatch.setattr("app.model_qa.review_post", review)
    async def schedule(db, post, slot=None):
        assert await prepare_post_qa(db, post)
        scheduled.append((post.id, slot))
        post.status = PostStatus.SCHEDULED
        post.scheduled_at = slot
        return True
    monkeypatch.setattr("worker.main._auto_schedule_post", schedule)
    await replenish_content(db)
    await replenish_content(db)
    assert len(scheduled) == 1
    jobs = list((await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "content_plan"))).scalars())
    assert len(jobs) == 1 and 1 <= jobs[0].payload["count"] <= 6


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["MANUAL", "stop", "disabled"])
async def test_replenisher_respects_policy_and_toggles(db, monkeypatch, restriction):
    from app.autonomy import current_policy
    from app.models import AutonomyPolicy
    from worker.content_pipeline import replenish_content
    await set_autonomy_mode(db, "MANUAL" if restriction == "MANUAL" else "AUTO")
    if restriction == "stop":
        policy = (await db.execute(select(AutonomyPolicy))).scalar_one()
        policy.emergency_stop = True
    if restriction == "disabled":
        db.add(AppSetting(key="auto_content_plan", value=False))
    await db.commit()
    await draft(db)
    async def forbidden(*args, **kw):
        raise AssertionError("Must not generate or schedule")
    monkeypatch.setattr("worker.main._auto_schedule_post", forbidden)
    await replenish_content(db)
    assert not list((await db.execute(select(BackgroundJob))).scalars())


@pytest.mark.asyncio
async def test_dashboard_contains_stock_report(client):
    response = await client.get("/api/dashboard")
    assert response.status_code == 200
    stock = response.json()["content_stock"]
    assert stock["target_days"] == 3 and stock["missing_slots"]
    assert stock["ready_scheduled"] == 0


@pytest.mark.asyncio
async def test_refill_generation_has_a_daily_budget(db):
    from worker.content_pipeline import replenish_content
    await set_autonomy_mode(db, "AUTO")
    for i in range(4):
        db.add(BackgroundJob(kind="content_plan", status=JobStatus.DONE, payload={}, idempotency_key=f"stock-content-plan:budget-{i}"))
    await db.commit()
    report = await replenish_content(db)
    assert report["refill_batches_today"] == 4
    assert len(list((await db.execute(select(BackgroundJob))).scalars())) == 4


@pytest.mark.asyncio
async def test_gap_alert_does_not_repeat_when_state_is_unchanged(db):
    from app.models import ActivityEvent
    from worker.content_pipeline import record_stock_alert
    report = await stock_report(db)
    await record_stock_alert(db, report)
    await record_stock_alert(db, report)
    alerts = list((await db.execute(select(ActivityEvent).where(ActivityEvent.action == "content_stock_gap"))).scalars())
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_edit_during_model_review_does_not_approve_the_wrong_version(db, monkeypatch):
    from sqlalchemy import update
    await set_autonomy_mode(db, "AUTO")
    post = await draft(db)
    async def review(*args, **kw):
        await db.execute(update(ThreadsPost).where(ThreadsPost.id == post.id).values(
            content=REPAIRED, version=2).execution_options(synchronize_session=False))
        return QaResult(True)
    monkeypatch.setattr("app.model_qa.review_post", review)
    assert not await prepare_post_qa(db, post)
    assert post.content == REPAIRED and post.version == 2
    assert not await qa_is_current(db, post)


@pytest.mark.asyncio
async def test_actual_scheduler_enqueues_the_repaired_version(db, monkeypatch):
    from app.config import settings
    from app.models import ThreadsAccount
    from worker.main import _auto_schedule_post
    await set_autonomy_mode(db, "AUTO")
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    db.add(ThreadsAccount(profile_id="prepared", username="recruiter", connected=True,
                          is_mock=False, access_token_encrypted="opaque"))
    await db.commit()
    post = await draft(db)
    async def review(text, facts, **kw):
        return QaResult(text == REPAIRED, () if text == REPAIRED else ("MACHINE_TONE",))
    async def rewrite(*args, **kw):
        return json.dumps({"content": REPAIRED}), "stop"
    monkeypatch.setattr("app.model_qa.review_post", review)
    monkeypatch.setattr(AIService, "_chat", rewrite)
    assert await _auto_schedule_post(db, post)
    job = (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "publish_post"))).scalar_one()
    assert post.status == PostStatus.SCHEDULED
    assert job.status == JobStatus.PENDING
    assert job.payload["expected_version"] == post.version == 2
    assert await qa_is_current(db, post)


@pytest.mark.asyncio
async def test_confirmed_account_contact_is_accepted_by_deterministic_gate(db):
    from app.post_validation import validate_post_for_publication
    from fastapi import HTTPException
    vacancy = Vacancy(title="Склад", description="Робота", location="Kraków", contact="+48 111 222 333")
    text = REPAIRED + " Телефон +48 444 555 666."
    with pytest.raises(HTTPException):
        validate_post_for_publication(content=text, language="uk", vacancy=vacancy, content_type="EDUCATIONAL")
    assert validate_post_for_publication(content=text, language="uk", vacancy=vacancy,
                                        content_type="EDUCATIONAL", account_contact="+48 444 555 666") == text


@pytest.mark.asyncio
async def test_review_fingerprint_survives_database_reload(db, monkeypatch):
    await set_autonomy_mode(db, "AUTO")
    post = await draft(db)
    async def review(*args, **kw):
        return QaResult(True)
    monkeypatch.setattr("app.model_qa.review_post", review)
    assert await prepare_post_qa(db, post)
    post_id = post.id
    await db.commit()
    db.expunge_all()
    stored = await db.get(ThreadsPost, post_id)
    assert await qa_is_current(db, stored)


@pytest.mark.asyncio
@pytest.mark.parametrize("issues", [None, ["UNKNOWN_CHECK"], [1]])
async def test_invalid_review_issues_cannot_clear_a_post(monkeypatch, issues):
    from app.model_qa import review_post
    async def chat(*args, **kw):
        return json.dumps({"verdict": "PASS", "issues": issues}), "stop"
    monkeypatch.setattr(AIService, "_chat", chat)
    assert not (await review_post(TEXT, [])).passed


@pytest.mark.asyncio
async def test_stock_generation_does_not_use_unconfirmed_vacancy_facts(db, monkeypatch):
    from worker.planner import content_plan
    db.add(Vacancy(title="Unverified", description="Test", location="Kraków"))
    await db.commit()
    received = []
    async def plan(self, **kwargs):
        received.extend(kwargs["vacancies"])
        return []
    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 2, "language": "uk", "stock_refill": True})
    assert received == []
