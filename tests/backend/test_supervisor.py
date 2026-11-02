from datetime import datetime, timedelta, timezone

import pytest

from app.enums import JobStatus, PostStatus, ContentType
from app.models import AppSetting, BackgroundJob, ThreadsAccount, ThreadsPost, Vacancy
from app.supervisor import scan_supervisor


@pytest.mark.asyncio
async def test_supervisor_api_exposes_read_and_guarded_repair_scan(client):
    read = await client.get("/api/threads/supervisor")
    assert read.status_code == 200
    assert read.json()["apply_fixes"] is False
    repair = await client.post("/api/threads/supervisor/scan")
    assert repair.status_code == 200
    assert repair.json()["apply_fixes"] is True


@pytest.mark.asyncio
async def test_supervisor_reports_unsupported_automation_and_missing_scopes(db):
    db.add(ThreadsAccount(
        profile_id="profile-1", username="salt", connected=True, is_mock=False,
        api_status="READY", permissions=["threads_basic", "threads_content_publish"],
    ))
    db.add(AppSetting(key="auto_external_comment", value=True))
    await db.commit()

    report = await scan_supervisor(db, apply_fixes=False)
    rules = {item["rule"] for item in report["findings"]}
    assert "meta_mentions_scope_missing" in rules
    assert "unsupported_external_comment_automation" in rules
    assert report["summary"]["fixed"] == 0


@pytest.mark.asyncio
async def test_supervisor_quarantines_overdue_schedule_without_publishing(db):
    post = ThreadsPost(
        content="Пост із підтвердженими умовами", content_type=ContentType.FAQ,
        language="uk", status=PostStatus.SCHEDULED,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db.add(post)
    await db.flush()
    job = BackgroundJob(
        kind="publish_post", payload={"post_id": post.id},
        idempotency_key="supervisor-test-schedule", status=JobStatus.PENDING,
        run_at=post.scheduled_at,
    )
    db.add(job)
    await db.commit()

    report = await scan_supervisor(db, apply_fixes=True)
    await db.refresh(post)
    await db.refresh(job)
    assert post.status == PostStatus.APPROVED
    assert post.scheduled_at is None
    assert job.status == JobStatus.FAILED
    assert any(item["rule"] == "overdue_schedule" and item["fixed"] for item in report["findings"])


@pytest.mark.asyncio
async def test_supervisor_quarantines_invalid_approved_post(db):
    post = ThreadsPost(
        content="Ставка: Не вказано в оголошенні", content_type=ContentType.VACANCY,
        language="uk", status=PostStatus.APPROVED,
    )
    db.add(post)
    await db.commit()

    report = await scan_supervisor(db, apply_fixes=True)
    await db.refresh(post)
    assert post.status == PostStatus.FAILED
    assert "Supervisor blocked publication" in (post.failure_reason or "")
    assert any(item["rule"] == "invalid_publish_candidate" and item["fixed"] for item in report["findings"])


@pytest.mark.asyncio
async def test_supervisor_recovers_stale_external_job_as_manual_check(db):
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    job = BackgroundJob(
        kind="publish_post", payload={"post_id": 99}, idempotency_key="supervisor-test-job",
        status=JobStatus.RUNNING, run_at=stale, locked_at=stale, attempts=1,
    )
    db.add(job)
    await db.commit()

    report = await scan_supervisor(db, apply_fixes=True)
    await db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "verify Threads" in (job.last_error or "")
    assert any(item["rule"] == "stale_running_job" and item["fixed"] for item in report["findings"])
