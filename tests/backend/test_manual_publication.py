from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.enums import JobStatus, PostStatus
from app.models import BackgroundJob, ThreadsPost, Vacancy
from tests.backend.conftest import set_autonomy_mode

TEXT = "Перед поїздкою складіть список запитань до рекрутера. Уточніть умови та попросіть пояснити незрозумілі пункти. Збережіть відповіді та порівняйте пропозиції перед остаточним рішенням."


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [PostStatus.APPROVED, PostStatus.SCHEDULED])
async def test_operator_can_publish_now_without_model_veto(client, db, monkeypatch, status):
    await set_autonomy_mode(db, "AUTO")
    vacancy = Vacancy(title="Склад", description="Робота", location="Kraków")
    db.add(vacancy)
    await db.flush()
    post = ThreadsPost(content=TEXT, content_type="EDUCATIONAL", language="uk", status=status,
                       vacancy_id=vacancy.id, model_qa_passed=False, model_qa_notes="Model rejected")
    db.add(post)
    await db.flush()
    if status == PostStatus.SCHEDULED:
        post.scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)
        db.add(BackgroundJob(kind="publish_post", payload={"post_id": post.id},
                            idempotency_key="old-schedule", run_at=post.scheduled_at))
    await db.commit()
    async def forbidden(*args, **kwargs):
        raise AssertionError("Manual publication must not invoke model QA")
    monkeypatch.setattr("app.model_qa.prepare_post_qa", forbidden)
    response = await client.post(f"/api/threads/posts/{post.id}/publish")
    assert response.status_code == 202
    jobs = list((await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "publish_post"))).scalars())
    active = [job for job in jobs if job.status == JobStatus.PENDING]
    assert len(active) == 1 and active[0].payload["human_initiated"] is True
    assert active[0].payload["expected_status"] == "APPROVED"
    assert post.scheduled_at is None
    if status == PostStatus.SCHEDULED:
        assert next(job for job in jobs if job.idempotency_key == "old-schedule").status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_source_confirmation_persists_and_wakes_preparation(client, db):
    vacancy = Vacancy(title="Склад", description="Робота", location="Kraków")
    db.add(vacancy)
    await db.commit()
    response = await client.post(f"/api/vacancies/{vacancy.id}/verify-source")
    assert response.status_code == 200
    assert response.json()["freshness"]["status"] == "FRESH"
    await db.refresh(vacancy)
    assert vacancy.source_confirmed_at is not None
    assert (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "prepare_content"))).scalar_one().status == JobStatus.PENDING
