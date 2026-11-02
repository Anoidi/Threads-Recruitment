"""Regression tests for the Autonomy Policy Engine.

The rules under test come from the brief:

* one source of truth - the mode lives in one row and the old flags are
  derived, never consulted;
* the check runs immediately before the action, so tightening the mode stops
  a publication that is already queued;
* an emergency stop halts every external action without stopping analytics;
* the check fails closed;
* Auto may not be entered while the freshness and model QA checks are missing;
* every decision is explained and audited.
"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import select

from app.autonomy import (
    ACTION_ANALYZE, ACTION_COLLECT, ACTION_PUBLISH, ACTION_REPLY, ACTION_SCHEDULE,
    ALLOW, DENY, MANUAL, REVIEW, SEMI_AUTO, PolicyBlocked,
    auto_available, current_policy, derived_flags, ensure_policy, evaluate,
    evaluate_and_record,
)
from app.enums import JobStatus, PostStatus
from app.models import (
    ActivityEvent, BackgroundJob, PolicyDecision, ReplyDraft, ThreadsInteraction, ThreadsPost,
)
from tests.backend.conftest import set_autonomy_mode


@pytest.mark.asyncio
async def test_policy_row_is_created_once_and_carries_the_mode(db):
    row = await ensure_policy(db)
    again = await ensure_policy(db)
    assert row.id == again.id
    policy = await current_policy(db)
    assert policy.mode in {MANUAL, SEMI_AUTO}


@pytest.mark.asyncio
async def test_read_only_actions_are_always_allowed(db):
    await set_autonomy_mode(db, MANUAL, emergency_stop=True)
    for action in (ACTION_COLLECT, ACTION_ANALYZE):
        decision = await evaluate(db, action=action)
        assert decision.decision == ALLOW, decision.reason


@pytest.mark.asyncio
async def test_emergency_stop_blocks_external_actions_but_not_analytics(db):
    await set_autonomy_mode(db, SEMI_AUTO, emergency_stop=True)
    publish = await evaluate(db, action=ACTION_PUBLISH, context={"approved": True})
    assert publish.decision == DENY
    assert "Emergency stop" in publish.reason
    analytics = await evaluate(db, action=ACTION_ANALYZE)
    assert analytics.decision == ALLOW


@pytest.mark.asyncio
async def test_manual_mode_requires_a_human_for_publishing_and_replying(db):
    await set_autonomy_mode(db, MANUAL)
    assert (await evaluate(db, action=ACTION_PUBLISH, context={"approved": True})).decision == REVIEW
    assert (await evaluate(db, action=ACTION_REPLY, context={"approved": True})).decision == REVIEW
    assert (await evaluate(db, action=ACTION_SCHEDULE, context={"approved": True})).decision == REVIEW


@pytest.mark.asyncio
async def test_semi_auto_publishes_approved_content_without_a_second_click(db):
    await set_autonomy_mode(db, SEMI_AUTO)
    decision = await evaluate(db, action=ACTION_PUBLISH, context={"approved": True})
    assert decision.decision == ALLOW


@pytest.mark.asyncio
async def test_semi_auto_holds_back_unapproved_content(db):
    await set_autonomy_mode(db, SEMI_AUTO)
    decision = await evaluate(db, action=ACTION_PUBLISH, context={"approved": False})
    assert decision.decision == REVIEW


@pytest.mark.asyncio
async def test_a_human_initiated_publication_is_allowed_in_manual_mode(db):
    await set_autonomy_mode(db, MANUAL)
    decision = await evaluate(db, action=ACTION_PUBLISH, context={"approved": True, "human_initiated": True})
    assert decision.decision == ALLOW


@pytest.mark.asyncio
async def test_high_risk_replies_always_go_to_a_human(db):
    await set_autonomy_mode(db, SEMI_AUTO)
    interaction = ThreadsInteraction(
        remote_id="r-1", kind="REPLY", username="candidate", message="Обращение",
        classification="SALARY_QUESTION", risk="HIGH",
    )
    decision = await evaluate(db, action=ACTION_REPLY, context={"interaction": interaction, "approved": True})
    assert decision.decision == DENY
    assert "High-risk" in decision.reason


@pytest.mark.asyncio
async def test_semi_auto_only_answers_topics_on_the_allowlist(db):
    await set_autonomy_mode(db, SEMI_AUTO, reply_topics=["SALARY_QUESTION"])
    interaction = ThreadsInteraction(
        remote_id="r-2", kind="REPLY", username="candidate", message="Питання",
        classification="HOUSING_QUESTION", risk="LOW",
    )
    decision = await evaluate(db, action=ACTION_REPLY, context={"interaction": interaction, "approved": True})
    assert decision.decision == REVIEW
    assert "allowlist" in decision.reason or "automatic answer list" in decision.reason


@pytest.mark.asyncio
async def test_auto_mode_is_refused_without_the_platform_permission(db):
    """Both checks exist now, so the platform permission is the last gate."""
    await set_autonomy_mode(db, MANUAL)
    policy = await current_policy(db)
    available, blockers = await auto_available(db, policy)
    assert available is False
    assert any("threads_content_publish" in item for item in blockers), blockers


@pytest.mark.asyncio
async def test_auto_mode_becomes_available_with_the_permission_granted(db):
    from app.models import ThreadsAccount

    await set_autonomy_mode(db, MANUAL)
    db.add(ThreadsAccount(
        profile_id="auto-profile", username="recruiter", connected=True, is_mock=False,
        permissions=["threads_basic", "threads_content_publish"],
    ))
    await db.commit()
    policy = await current_policy(db)
    available, blockers = await auto_available(db, policy)
    assert available is True, blockers
    assert blockers == []


@pytest.mark.asyncio
async def test_the_policy_check_fails_closed(db, monkeypatch):
    import app.autonomy as autonomy

    async def broken(_db):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(autonomy, "current_policy", broken)
    decision = await autonomy.evaluate(db, action=ACTION_PUBLISH, context={"approved": True})
    assert decision.decision == DENY
    assert "failing closed" in decision.reason


@pytest.mark.asyncio
async def test_every_decision_is_audited_with_the_policy_version(db):
    await set_autonomy_mode(db, MANUAL)
    decision = await evaluate_and_record(db, action=ACTION_PUBLISH, subject_type="post", subject_id=7, job_id=9)
    await db.commit()

    row = (await db.execute(select(PolicyDecision).order_by(PolicyDecision.id.desc()).limit(1))).scalar_one()
    assert row.action == ACTION_PUBLISH
    assert row.decision == decision.decision
    assert row.reason
    assert row.policy_version == decision.version
    assert row.subject_id == 7
    assert row.job_id == 9


@pytest.mark.asyncio
async def test_the_old_flags_are_derived_not_a_second_source_of_truth(db):
    await set_autonomy_mode(db, SEMI_AUTO)
    policy = await current_policy(db)
    assert derived_flags(policy)["auto_post"] is True
    await set_autonomy_mode(db, MANUAL)
    policy = await current_policy(db)
    assert derived_flags(policy)["auto_post"] is False


@pytest.mark.asyncio
async def test_tightening_the_mode_stops_a_queued_publication(client, db):
    """The check happens at execution time, not when the job was queued."""
    await set_autonomy_mode(db, SEMI_AUTO)
    post = (await client.post("/api/threads/posts", json={
        "content": "Approved factual post about the rate and the schedule",
        "content_type": "EDUCATIONAL", "idempotency_key": "policy-race-1",
    })).json()
    await client.post(f"/api/threads/posts/{post['id']}/review")
    await client.post(f"/api/threads/posts/{post['id']}/approve", json={"expected_version": post["version"]})
    await db.commit()

    from worker.main import publish_post

    job = (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "publish_post").order_by(BackgroundJob.id.desc()).limit(1))).scalar_one()
    # The operator tightens the mode while the publication is waiting.
    await set_autonomy_mode(db, MANUAL)

    with pytest.raises(PolicyBlocked):
        await publish_post(db, post["id"], "SCHEDULED", post["version"], job.id)

    stored = await db.get(ThreadsPost, post["id"])
    await db.refresh(stored)
    assert stored.status == PostStatus.APPROVED
    assert stored.scheduled_at is None
    assert "autonomy policy" in (stored.failure_reason or "")

    decision = (await db.execute(select(PolicyDecision).order_by(PolicyDecision.id.desc()).limit(1))).scalar_one()
    assert decision.decision == REVIEW


@pytest.mark.asyncio
async def test_a_mode_change_is_audited(client, db):
    await set_autonomy_mode(db, MANUAL)
    response = await client.patch("/api/threads/autonomy", json={"mode": SEMI_AUTO, "actor": "operator", "reason": "проверка"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == SEMI_AUTO
    assert body["version"] >= 2

    event = (await db.execute(
        select(ActivityEvent).where(ActivityEvent.action == "autonomy_policy_changed")
    )).scalar_one()
    assert event.detail["from_mode"] == MANUAL
    assert event.detail["to_mode"] == SEMI_AUTO
    assert event.detail["actor"] == "operator"


@pytest.mark.asyncio
async def test_auto_mode_cannot_be_switched_on_without_the_permission(client, db):
    await set_autonomy_mode(db, MANUAL)
    response = await client.patch("/api/threads/autonomy", json={"mode": "AUTO"})
    assert response.status_code == 409
    assert "not available" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_daily_limits_are_enforced(client, db):
    await set_autonomy_mode(db, SEMI_AUTO, limits={"max_posts_per_day": 1, "max_publish_per_hour": 5})
    now = datetime.now(timezone.utc)
    db.add(ThreadsPost(
        content="Already published", content_type="EDUCATIONAL", language="uk",
        status=PostStatus.PUBLISHED, remote_id="remote-old", published_at=now,
    ))
    await db.commit()

    decision = await evaluate(db, action=ACTION_PUBLISH, context={"approved": True})
    assert decision.decision == DENY
    assert "limit" in decision.reason.lower()
