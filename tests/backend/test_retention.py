"""Audit I-7/D13: the DB had no retention policy.

`ActivityEvent`, `PolicyDecision`, finished `BackgroundJob` rows and old
`AgentRun` rows only grew. Weekly cleanup bounds them without ever touching
live content (vacancies, posts, candidates, tokens). Deletion is disabled in
configuration by default (fail-closed, mass deletion is an owner decision):
the weekly task then only reports the counts it would remove.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import worker.main as worker_main
from app.config import settings
from app.enums import JobStatus
from app.models import ActivityEvent, AgentRun, AppSetting, BackgroundJob, PolicyDecision
from worker.main import retention_cleanup_in_session, schedule_periodic


class SameSession:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


def _old(days: int):
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.mark.asyncio
async def test_disabled_by_default_reports_without_deleting(db, monkeypatch):
    monkeypatch.setattr(settings, "retention_enabled", False)
    db.add_all([
        ActivityEvent(action="post_published", detail={}, created_at=_old(400)),
        ActivityEvent(action="post_published", detail={}, created_at=datetime.now(timezone.utc)),
        PolicyDecision(action="publish", decision="ALLOW", reason="ok", mode="MANUAL", created_at=_old(190)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-old-done",
                      status=JobStatus.DONE, updated_at=_old(60)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-old-running",
                      status=JobStatus.RUNNING, updated_at=_old(60)),
        AgentRun(role="SCOUT", status="COMPLETED", finished_at=_old(120)),
        AgentRun(role="SCOUT", status="RUNNING"),
    ])
    await db.commit()

    planned = await retention_cleanup_in_session(db, datetime.now(timezone.utc))

    assert planned["activity_events"] == 1
    assert planned["policy_decisions"] == 1
    assert planned["background_jobs"] == 1  # the DONE one; RUNNING survives
    assert planned["agent_runs"] == 1
    assert (await db.execute(select(func.count(ActivityEvent.id)))).scalar_one() == 2
    assert (await db.execute(select(func.count(PolicyDecision.id)))).scalar_one() == 1
    assert (await db.execute(select(func.count(BackgroundJob.id)))).scalar_one() == 2
    assert (await db.execute(select(func.count(AgentRun.id)))).scalar_one() == 2


@pytest.mark.asyncio
async def test_enabled_retention_deletes_only_the_old(db, monkeypatch):
    monkeypatch.setattr(settings, "retention_enabled", True)
    db.add_all([
        ActivityEvent(action="a", detail={"old": True}, created_at=_old(100)),
        ActivityEvent(action="a", detail={"old": False}),
        PolicyDecision(action="p", decision="ALLOW", reason="ok", mode="MANUAL", created_at=_old(190)),
        PolicyDecision(action="p", decision="ALLOW", reason="ok", mode="MANUAL", created_at=_old(10)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-done-old",
                      status=JobStatus.DONE, updated_at=_old(40)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-failed-old",
                      status=JobStatus.FAILED, updated_at=_old(40)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-done-fresh",
                      status=JobStatus.DONE, updated_at=_old(5)),
        BackgroundJob(kind="sync_community", payload={}, idempotency_key="r-pending-old",
                      status=JobStatus.PENDING, updated_at=_old(40)),
        AgentRun(role="WRITER", status="COMPLETED", finished_at=_old(100)),
        AgentRun(role="WRITER", status="FAILED", finished_at=_old(10)),
        AgentRun(role="WRITER", status="RUNNING", started_at=_old(100)),
    ])
    await db.commit()

    removed = await retention_cleanup_in_session(db, datetime.now(timezone.utc))

    assert removed["activity_events"] == 1
    assert removed["policy_decisions"] == 1
    assert removed["background_jobs"] == 2
    assert removed["agent_runs"] == 1
    remaining_activity = list((await db.execute(select(ActivityEvent))).scalars())
    assert len(remaining_activity) == 1 and remaining_activity[0].detail == {"old": False}
    remaining_jobs = list((await db.execute(select(BackgroundJob))).scalars())
    assert sorted(job.idempotency_key for job in remaining_jobs) == ["r-done-fresh", "r-pending-old"]
    remaining_runs = list((await db.execute(select(AgentRun))).scalars())
    assert sorted(run.status for run in remaining_runs) == ["FAILED", "RUNNING"]


@pytest.mark.asyncio
async def test_batches_flush_the_whole_backlog(db, monkeypatch):
    """More rows than one batch (1000) must not leave a remainder behind."""
    monkeypatch.setattr(settings, "retention_enabled", True)
    db.add_all([ActivityEvent(action="flood", detail={}, created_at=_old(200)) for _ in range(1005)])
    await db.commit()

    removed = await retention_cleanup_in_session(db, datetime.now(timezone.utc))

    assert removed["activity_events"] == 1005
    assert (await db.execute(select(ActivityEvent))).scalars().first() is None


@pytest.mark.asyncio
async def test_retention_is_scheduled_once_per_iso_week(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_channel", "")
    # Suppress the other recurring kinds that follow their own cadence; the
    # test only cares about the retention line.
    call_kwargs: list[dict] = []

    async def fake_enqueue(db, kind, payload=None, *, run_at=None, idempotency_key=None):
        call_kwargs.append({"kind": kind, "idempotency_key": idempotency_key})
        job = BackgroundJob(kind=kind, payload=payload if isinstance(payload, dict) else {},
                            idempotency_key=idempotency_key or "x", status=JobStatus.PENDING)
        db.add(job)
        await db.flush()
        return job

    monkeypatch.setattr(worker_main, "enqueue", fake_enqueue)
    now = datetime.now(timezone.utc)
    await schedule_periodic(db)
    kind_keys = [(c["kind"], c["idempotency_key"]) for c in call_kwargs if c["kind"] == "retention"]
    assert ("retention", f"retention:{now.strftime('%G-W%V')}") in kind_keys

    # A second heartbeat in the same week must not enqueue it again.
    call_kwargs.clear()
    await schedule_periodic(db)
    assert [c for c in call_kwargs if c["kind"] == "retention"] == []


@pytest.mark.asyncio
async def test_process_job_runs_retention_and_marks_it_done(db, monkeypatch):
    db.add_all([
        ActivityEvent(action="old", detail={}, created_at=_old(200)),
    ])
    await db.commit()
    job = BackgroundJob(kind="retention", payload={}, idempotency_key="retention:test", status=JobStatus.PENDING)
    db.add(job)
    await db.commit()

    class JobSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker_main, "SessionLocal", lambda: JobSession())

    await worker_main.process_job(job.id)

    await db.refresh(job)
    assert job.status == JobStatus.DONE

