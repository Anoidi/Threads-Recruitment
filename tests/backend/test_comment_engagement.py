from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.comment_engagement import MentionManager, generate_variants, validate_engagement_text
from app.models import AppSetting, CommentEngagementCampaign, CommentEngagementVariant, ThreadsInteraction, ThreadsPost
from app.threads_client import LiveThreadsClient
from worker.main import sync_community


def test_engagement_variants_are_ukrainian_bounded_and_source_grounded():
    variants = generate_variants(username="Candidate_1", comment_text="Чи є житло?", topic="housing", source_context="Локація: Краків")
    assert {item["style"] for item in variants} == {"moderate", "sarcastic", "discussion"}
    assert all(0 < item["character_count"] <= 500 for item in variants)
    assert all("@candidate_1" in item["content"] for item in variants)
    assert all("Краків" in item["content"] for item in variants)
    with pytest.raises(ValueError):
        validate_engagement_text("@candidate_1, ти дурний. Пиши?", expected_usernames=["candidate_1"])


def test_mention_manager_deduplicates_opt_out_and_cooldown():
    now = datetime.now(timezone.utc)
    manager = MentionManager(cooldown_hours=24, max_group_size=3)
    result = manager.select(
        ["Alice", "@alice", "Bob", "Carol", "Dave"],
        opted_out=["carol"],
        history=[{"normalized_username": "bob", "created_at": now - timedelta(hours=2)}],
        now=now,
    )
    assert result == ["alice", "dave"]


@pytest.mark.asyncio
async def test_live_client_follows_cursor_and_conversation_tree(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "user-1", "username": "owner"})
        if request.url.path.endswith("/user-1/threads"):
            if request.url.params.get("after"):
                return httpx.Response(200, json={"data": [{"id": "post-2", "has_replies": True}], "paging": {"cursors": {}}})
            return httpx.Response(200, json={"data": [{"id": "post-1", "has_replies": True}], "paging": {"cursors": {"after": "next"}}})
        if request.url.path.endswith("/post-1/conversation"):
            return httpx.Response(200, json={"data": [{"id": "r1", "username": "candidate", "text": "Чи є житло?", "timestamp": "2026-10-13T10:00:00+00:00", "replied_to": {"id": "post-1"}, "root_post": {"id": "post-1"}}], "paging": {"cursors": {}}})
        if request.url.path.endswith("/post-2/conversation"):
            return httpx.Response(200, json={"data": [{"id": "r2", "username": "owner", "text": "Мій власний коментар", "timestamp": "2026-10-13T10:00:00+00:00", "is_reply_owned_by_me": True, "replied_to": {"id": "post-2"}, "root_post": {"id": "post-2"}}], "paging": {"cursors": {}}})
        raise AssertionError(request.url)

    monkeypatch.setattr("app.threads_client.asyncio.sleep", AsyncMock())
    client = LiveThreadsClient("secret", "user-1", transport=httpx.MockTransport(handler))
    result = await client.replies()
    await client.aclose()
    assert [item["id"] for item in result] == ["r1"]
    assert "post-1/conversation" in " ".join(calls)


@pytest.mark.asyncio
async def test_sync_persists_analysis_variants_and_restart_dedupes(db):
    db.add(AppSetting(key="comment_engagement_mode", value="DRAFT"))
    post = ThreadsPost(content="Робота в Кракові. Питаєте про житло.", remote_id="mock-remote-post-1", status="PUBLISHED", content_type="ENGAGEMENT", language="uk")
    db.add(post)
    await db.commit()
    await sync_community(db)
    await sync_community(db)
    campaigns = list((await db.execute(select(CommentEngagementCampaign))).scalars())
    variants = list((await db.execute(select(CommentEngagementVariant))).scalars())
    assert len(campaigns) == 3
    assert len(variants) == 9
    interaction = (await db.execute(select(ThreadsInteraction).where(ThreadsInteraction.remote_id == "mock-reply-1"))).scalar_one()
    assert interaction.post_id == post.id


@pytest.mark.asyncio
async def test_comment_engagement_api_files_approved_text_as_review_draft(client, db):
    interaction = ThreadsInteraction(remote_id="real-comment-1", kind="REPLY", username="candidate_api", message="Чи є житло?", remote_parent_id="post-api", needs_review=True)
    db.add(interaction)
    await db.commit()
    generated = await client.post("/api/threads/comment-engagement/generate", json={"interaction_id": interaction.id, "mode": "DRAFT"})
    assert generated.status_code == 200
    payload = generated.json()
    assert len(payload["variants"]) == 3
    variant = payload["variants"][0]
    approved = await client.post(f"/api/threads/comment-engagement/{payload['id']}/approve", json={"variant_id": variant["id"]})
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    posts = list((await db.execute(select(ThreadsPost).where(ThreadsPost.content_type == "ENGAGEMENT"))).scalars())
    assert len(posts) == 1
    assert posts[0].status == "REVIEW"

