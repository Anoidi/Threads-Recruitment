from datetime import datetime, timedelta, timezone
import pytest
from app.enums import JobStatus, PostStatus
from app.models import BackgroundJob, ThreadsPost
from worker.main import prolong_job_lock, recover_stale_jobs_in_session
from app.config import settings


@pytest.mark.asyncio
async def test_uncertain_publication_is_not_automatically_resent(db):
    now = datetime.now(timezone.utc)
    post = ThreadsPost(content='Already sending', content_type='FAQ', language='uk', status=PostStatus.PUBLISHING)
    db.add(post)
    await db.flush()
    job = BackgroundJob(kind='publish_post', payload={'post_id': post.id}, idempotency_key='uncertain', status=JobStatus.RUNNING, run_at=now, locked_at=now-timedelta(minutes=11), attempts=1)
    db.add(job)
    await db.flush()
    assert await recover_stale_jobs_in_session(db, now) == 1
    assert job.status == JobStatus.FAILED
    assert post.status == PostStatus.FAILED
    assert 'Check Threads' in post.failure_reason
    assert await recover_stale_jobs_in_session(db, now) == 0


@pytest.mark.asyncio
async def test_safe_recovery_is_bounded_and_does_not_touch_current_locks(db):
    now = datetime.now(timezone.utc)
    jobs = [BackgroundJob(kind='collect_metrics', payload={}, idempotency_key=f'safe-{i}', status=JobStatus.RUNNING, run_at=now, locked_at=now-timedelta(minutes=minutes), attempts=attempts) for i, (minutes, attempts) in enumerate([(11, 1), (11, 3), (2, 1)])]
    db.add_all(jobs)
    await db.flush()
    assert await recover_stale_jobs_in_session(db, now) == 2
    assert [j.status for j in jobs] == [JobStatus.PENDING, JobStatus.FAILED, JobStatus.RUNNING]


@pytest.mark.asyncio
async def test_long_media_job_is_not_reclaimed_before_its_timeout(db, monkeypatch):
    monkeypatch.setattr(settings, 'comfyui_timeout_seconds', 1800)
    now = datetime.now(timezone.utc)
    job = BackgroundJob(kind='generate_media', payload={}, idempotency_key='long-media', status=JobStatus.RUNNING, run_at=now, locked_at=now-timedelta(minutes=15), attempts=1)
    db.add(job)
    await db.flush()
    assert await recover_stale_jobs_in_session(db, now) == 0
    assert job.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_prolonged_lock_keeps_a_long_job_safe_from_recovery(db, monkeypatch):
    """Audit I-3: while the worker heartbeats a long agent run, the durable
    lock must be prolonged so recovery does not double-execute it."""
    import worker.main as worker_main

    now = datetime.now(timezone.utc)
    job = BackgroundJob(kind='run_agent', payload={'run_id': 1}, idempotency_key='prolong-1',
                        status=JobStatus.RUNNING, run_at=now, locked_at=now-timedelta(minutes=15), attempts=1)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: SameSession())

    await prolong_job_lock(job.id)
    await db.refresh(job)
    assert job.status == JobStatus.RUNNING
    run_norm = job.run_at.replace(tzinfo=timezone.utc) if job.run_at.tzinfo is None else job.run_at
    assert run_norm <= now  # untouched schedule
    locked = job.locked_at.replace(tzinfo=timezone.utc) if job.locked_at.tzinfo is None else job.locked_at
    assert (datetime.now(timezone.utc) - locked).total_seconds() < 10

    recovered = await recover_stale_jobs_in_session(db, datetime.now(timezone.utc))
    assert recovered == 0
    assert job.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_without_prolongation_a_stale_lock_is_recovered(db):
    """The counter-case end-to-end: no heartbeat prolongation → recovery keeps
    its old behaviour, so the test above cannot pass trivially."""
    now = datetime.now(timezone.utc)
    job = BackgroundJob(kind='run_agent', payload={'run_id': 2}, idempotency_key='prolong-2',
                        status=JobStatus.RUNNING, run_at=now, locked_at=now-timedelta(minutes=15), attempts=0)
    db.add(job)
    await db.flush()
    assert await recover_stale_jobs_in_session(db, now) == 1
    assert job.status == JobStatus.PENDING
    assert job.last_error == 'Recovered stale worker lock'
