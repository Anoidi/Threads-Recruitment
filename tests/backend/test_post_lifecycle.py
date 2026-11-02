from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.enums import JobStatus
from app.models import BackgroundJob
from app.schemas import PostRead


async def approved_post(client, vacancy):
    generated = (await client.post(
        "/api/threads/posts/generate",
        json={"vacancy_id": vacancy["id"]},
    )).json()
    await client.post(f"/api/threads/posts/{generated['id']}/review")
    approved = await client.post(
        f"/api/threads/posts/{generated['id']}/approve",
        json={"expected_version": generated["version"]},
    )
    assert approved.status_code == 200
    return approved.json()


def test_post_read_serializes_mysql_naive_datetimes_as_utc():
    """MySQL returns DATETIME values without tzinfo; API timestamps stay unambiguous."""
    naive = datetime(2026, 10, 8, 7, 30)
    post = PostRead.model_validate({
        "id": 1,
        "version": 1,
        "vacancy_id": None,
        "account_id": None,
        "content": "Scheduled content",
        "hook": None,
        "content_type": "EDUCATIONAL",
        "language": "en",
        "status": "SCHEDULED",
        "reasoning_summary": None,
        "scheduled_at": naive,
        "published_at": None,
        "remote_id": None,
        "failure_reason": None,
        "created_at": naive,
        "updated_at": naive,
    }).model_dump(mode="json")

    for field in ("scheduled_at", "created_at", "updated_at"):
        parsed = datetime.fromisoformat(post[field].replace("Z", "+00:00"))
        assert parsed.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_human_approval_is_mandatory(client, vacancy):
    generated = await client.post("/api/threads/posts/generate", json={"vacancy_id": vacancy["id"], "content_type": "VACANCY", "language": "en"})
    assert generated.status_code == 201
    post = generated.json()
    assert post["status"] == "AI_GENERATED"
    blocked = await client.post(f"/api/threads/posts/{post['id']}/approve", json={"expected_version": post["version"]})
    assert blocked.status_code == 409
    reviewed = await client.post(f"/api/threads/posts/{post['id']}/review")
    assert reviewed.json()["status"] == "REVIEW"
    approved = await client.post(f"/api/threads/posts/{post['id']}/approve", json={"expected_version": post["version"]})
    assert approved.json()["status"] == "APPROVED"
    scheduled = await client.post(f"/api/threads/posts/{post['id']}/schedule", json={"scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "idempotency_key": "schedule-test-1"})
    assert scheduled.status_code == 200
    assert scheduled.json()["status"] == "SCHEDULED"


@pytest.mark.asyncio
async def test_general_ai_topic_does_not_require_vacancy(client):
    generated = await client.post(
        "/api/threads/posts/generate",
        json={"content_type": "EDUCATIONAL", "language": "uk", "idea": "Як перевірити умови роботи"},
    )
    assert generated.status_code == 201
    assert generated.json()["vacancy_id"] is None
    assert generated.json()["status"] == "AI_GENERATED"


@pytest.mark.asyncio
async def test_manual_post_idempotency_and_invalid_transition(client):
    data = {"content": "A factual recruitment post", "content_type": "EDUCATIONAL", "language": "en", "idempotency_key": "same-request"}
    first = await client.post("/api/threads/posts", json=data)
    second = await client.post("/api/threads/posts", json=data)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    bad = await client.post(f"/api/threads/posts/{first.json()['id']}/publish")
    assert bad.status_code == 409


@pytest.mark.asyncio
async def test_cancelling_schedule_cancels_pending_publication_job(client, vacancy, db):
    post = await approved_post(client, vacancy)
    scheduled = await client.post(
        f"/api/threads/posts/{post['id']}/schedule",
        json={"scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    )
    assert scheduled.status_code == 200

    cancelled = await client.post(f"/api/threads/posts/{post['id']}/cancel-schedule")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "APPROVED"
    assert cancelled.json()["scheduled_at"] is None

    jobs = list((await db.execute(select(BackgroundJob).where(
        BackgroundJob.kind == "publish_post",
    ))).scalars())
    matching = [job for job in jobs if job.payload.get("post_id") == post["id"]]
    assert matching
    assert all(job.status == JobStatus.FAILED for job in matching)
    assert all(job.last_error == "Cancelled by operator" for job in matching)


@pytest.mark.asyncio
async def test_rescheduling_leaves_exactly_one_pending_publication_job(client, vacancy, db):
    post = await approved_post(client, vacancy)
    first_time = datetime.now(timezone.utc) + timedelta(hours=1)
    second_time = first_time + timedelta(hours=2)

    first = await client.post(
        f"/api/threads/posts/{post['id']}/schedule",
        json={"scheduled_at": first_time.isoformat()},
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/threads/posts/{post['id']}/schedule",
        json={"scheduled_at": second_time.isoformat()},
    )
    assert second.status_code == 200

    jobs = list((await db.execute(select(BackgroundJob).where(
        BackgroundJob.kind == "publish_post",
    ))).scalars())
    matching = [job for job in jobs if job.payload.get("post_id") == post["id"]]
    pending = [job for job in matching if job.status == JobStatus.PENDING]
    superseded = [job for job in matching if job.status == JobStatus.FAILED]

    assert len(pending) == 1
    assert pending[0].run_at.replace(tzinfo=timezone.utc) == second_time
    assert len(superseded) == 1
    assert superseded[0].last_error == "Superseded by reschedule"

