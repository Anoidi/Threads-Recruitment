"""Regression tests for the community sync and metric-collector failure modes.

Background: the reply collector passed the sync cursor into the post list.
Meta applies that parameter to post publication time, so every reply that
arrived more than an hour after its post was published was hidden - and the
inbox looked empty while Threads held dozens of real comments. The metric
collector retried insights for media ids the platform can no longer load,
producing a permanent stream of failed jobs.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import worker.main as worker_main
from app.config import settings
from app.enums import MetricsStatus, PostStatus
from app.models import (
    ActivityEvent, AppSetting, BackgroundJob, PostMetric, ThreadsAccount,
    ThreadsInteraction, ThreadsPost,
)
from app.quality_guard import run_quality_guard
from app.threads_client import encrypt_token
from worker.main import collect_metrics, sync_community


class FakeThreadsClient:
    """Live-shaped adapter double that respects Meta's `since` semantics."""

    def __init__(self, posts, replies, cursor_dropped: int = 0):
        self.posts = posts
        self.reply_items = replies
        self.cursor_dropped = cursor_dropped
        self.reply_calls = 0
        self.skipped: set[str] = set()

    async def profile(self):
        return {"id": "user-1", "username": "recruiter"}

    async def own_posts(self):
        return list(self.posts)

    async def replies(self, since=None, *, skip_media_ids=None, posts=None):
        self.reply_calls += 1
        self.skipped = set(skip_media_ids or set())
        return list(self.reply_items)

    async def mentions(self, since=None):
        return []

    async def metrics(self, remote_post_id):
        return {"views": 1, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0}

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_sync_records_a_reply_to_an_older_post_and_reports_the_counters(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    account = ThreadsAccount(
        profile_id="user-1", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("test-token"),
        permissions=["threads_basic"], api_status="READY",
    )
    post = ThreadsPost(
        content="Vacancy post", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-1",
        published_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    db.add_all([account, post])
    await db.commit()

    reply = {
        "id": "reply-1", "username": "candidate", "text": "Чи є житло і яка ставка?",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parent_id": "post-1", "root_post_id": "post-1",
    }
    client = FakeThreadsClient(
        posts=[{"id": "post-1", "timestamp": post.published_at.isoformat(), "has_replies": True, "text": "Vacancy post"}],
        replies=[reply],
    )
    monkeypatch.setattr(worker_main, "get_threads_client", lambda *a, **k: client)

    await sync_community(db)

    interactions = list((await db.execute(select(ThreadsInteraction))).scalars())
    assert [item.remote_id for item in interactions] == ["reply-1"]
    # The reply is attributed to the post it belongs to.
    assert interactions[0].post_id == post.id

    event = (await db.execute(select(ActivityEvent).where(ActivityEvent.action == "community_sync_completed"))).scalar_one()
    assert event.detail["replies_created"] == 1
    assert event.detail["posts_with_replies"] == 1
    assert event.detail["dropped_by_cursor"] == 0
    # The conversation was read, so the next cycle must not read it again.
    await db.refresh(post)
    assert post.last_reply_checked_at is not None


@pytest.mark.asyncio
async def test_sync_skips_a_conversation_that_was_read_recently(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    account = ThreadsAccount(
        profile_id="user-1", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("test-token"),
        permissions=["threads_basic"], api_status="READY",
    )
    post = ThreadsPost(
        content="Vacancy post", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-1",
        published_at=datetime.now(timezone.utc) - timedelta(days=8),
        last_reply_checked_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    db.add_all([account, post])
    await db.commit()

    client = FakeThreadsClient(
        posts=[{"id": "post-1", "timestamp": post.published_at.isoformat(), "has_replies": True, "text": "Vacancy post"}],
        replies=[],
    )
    monkeypatch.setattr(worker_main, "get_threads_client", lambda *a, **k: client)

    await sync_community(db)

    assert client.skipped == {"post-1"}
    event = (await db.execute(select(ActivityEvent).where(ActivityEvent.action == "community_sync_completed"))).scalar_one()
    assert event.detail["posts_skipped_recently"] == 1


@pytest.mark.asyncio
async def test_sync_adopts_a_post_published_outside_the_manager(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    account = ThreadsAccount(
        profile_id="user-1", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("test-token"),
        permissions=["threads_basic"], api_status="READY",
    )
    db.add(account)
    await db.commit()

    reply = {
        "id": "reply-1", "username": "candidate", "text": "Питання про ставку",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parent_id": "post-9", "root_post_id": "post-9",
    }
    client = FakeThreadsClient(
        posts=[{"id": "post-9", "timestamp": datetime.now(timezone.utc).isoformat(), "has_replies": True,
                "text": "Шукаємо працівника складу. Ставка 30 zł/год."}],
        replies=[reply],
    )
    monkeypatch.setattr(worker_main, "get_threads_client", lambda *a, **k: client)

    await sync_community(db)

    adopted = (await db.execute(select(ThreadsPost).where(ThreadsPost.remote_id == "post-9"))).scalar_one()
    assert adopted.status == PostStatus.PUBLISHED
    assert adopted.content_type == "VACANCY"
    assert "Adopted from Threads" in (adopted.reasoning_summary or "")
    interaction = (await db.execute(select(ThreadsInteraction).where(ThreadsInteraction.remote_id == "reply-1"))).scalar_one()
    assert interaction.post_id == adopted.id


@pytest.mark.asyncio
async def test_unloadable_metrics_mark_the_post_and_stop_the_retry_loop(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    account = ThreadsAccount(
        profile_id="user-1", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("test-token"),
        permissions=["threads_basic"], api_status="READY",
    )
    post = ThreadsPost(
        content="Vacancy post", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-foreign",
    )
    db.add_all([account, post])
    await db.commit()

    class UnloadableClient(FakeThreadsClient):
        async def metrics(self, remote_post_id):
            from app.threads_client import ThreadsAPIError
            raise ThreadsAPIError(
                "Threads cannot load this object with the current account",
                status_code=400, category="not_owned",
            )

    monkeypatch.setattr(worker_main, "get_threads_client", lambda *a, **k: UnloadableClient([], []))

    await collect_metrics(db, post.id)

    await db.refresh(post)
    assert post.metrics_status == MetricsStatus.UNAVAILABLE
    assert post.metrics_note
    assert (await db.execute(select(PostMetric))).scalars().first() is None
    event = (await db.execute(select(ActivityEvent).where(ActivityEvent.action == "post_metrics_unavailable"))).scalar_one()
    assert event.detail["category"] == "not_owned"


@pytest.mark.asyncio
async def test_metric_refresh_skips_posts_marked_unavailable(db, monkeypatch):
    from worker.main import _schedule_metric_refreshes

    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    account = ThreadsAccount(
        profile_id="user-1", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("test-token"),
        permissions=["threads_basic"], api_status="READY",
    )
    dead = ThreadsPost(
        content="Old post", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-dead",
        metrics_status=MetricsStatus.UNAVAILABLE,
        metrics_note="owned by another account",
    )
    alive = ThreadsPost(
        content="New post", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-alive",
        published_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db.add_all([account, dead, alive])
    await db.commit()

    queued = await _schedule_metric_refreshes(db, datetime.now(timezone.utc), 60)

    jobs = list((await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "collect_metrics"))).scalars())
    assert len(jobs) == 1
    assert jobs[0].payload == {"post_id": alive.id}


@pytest.mark.asyncio
async def test_quality_guard_scan_persists_its_report_and_audit_event(db):
    report = await run_quality_guard(db)

    assert report["summary"]["mutations"] == 0
    stored = (await db.execute(select(AppSetting).where(AppSetting.key == "quality_guard_report"))).scalar_one()
    assert stored.value["summary"]["mutations"] == 0
    event = (await db.execute(select(ActivityEvent).where(ActivityEvent.action == "quality_guard_scan"))).scalar_one()
    assert event.category == "threads"


@pytest.mark.asyncio
async def test_unavailable_posts_are_excluded_from_the_comparison(db):
    from app.content_analysis import analyze_content

    measured = ThreadsPost(
        content="Measured", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-measured",
        published_at=datetime.now(timezone.utc) - timedelta(hours=72),
    )
    dead = ThreadsPost(
        content="Unmeasurable", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="post-dead",
        published_at=datetime.now(timezone.utc) - timedelta(hours=72),
        metrics_status=MetricsStatus.UNAVAILABLE,
    )
    db.add_all([measured, dead])
    await db.flush()
    snapshot = PostMetric(
        post_id=measured.id, views=100, likes=5, replies=2, reposts=0, quotes=0,
        captured_at=datetime.now(timezone.utc),
    )
    db.add(snapshot)
    await db.commit()

    report = analyze_content([measured, dead], [snapshot], {})

    assert report["published_posts"] == 1
    assert report["posts_metrics_unavailable"] == 1
