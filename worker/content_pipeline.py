"""Bounded preparation and replenishment in the agent worker, before publication."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.autonomy import current_policy
from app.content_stock import stock_report, stock_config, utc
from app.content_subjects import primary_subject
from app.enums import JobStatus, PostStatus
from app.models import AppSetting, BackgroundJob, PostApproval, ThreadsPost
from app.model_qa import qa_is_current
from app.queue import enqueue
from app.services import activity


async def remove_from_queue(db, post, reason):
    jobs = list((await db.execute(select(BackgroundJob).where(
        BackgroundJob.kind == "publish_post", BackgroundJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING))
    ).with_for_update())).scalars())
    matching = [j for j in jobs if j.payload.get("post_id") == post.id]
    if any(j.status == JobStatus.RUNNING for j in matching):
        return False
    for job in matching:
        job.status = JobStatus.FAILED
        job.last_error = reason
    post.status = PostStatus.AI_GENERATED
    post.scheduled_at = None
    post.failure_reason = reason
    await activity(db, "post_preparation_invalidated", "post", post.id, {"reason": reason})
    await db.commit()
    return True


async def record_stock_alert(db, report):
    state = {
        "missing_slots": [s.isoformat() for s in report["missing_slots"] if s <= datetime.now(timezone.utc) + timedelta(hours=24)],
        "stale": sorted(report["stale_scheduled_ids"]),
    }
    row = (await db.execute(select(AppSetting).where(AppSetting.key == "content_stock_alert"))).scalar_one_or_none()
    previous = row.value if row else None
    if state != previous:
        action = "content_stock_gap" if state["missing_slots"] or state["stale"] else "content_stock_recovered"
        if previous is not None or action == "content_stock_gap":
            await activity(db, action, detail={**state, "ready": report["ready_scheduled"],
                                             "rejected": report["rejected_count"]})
        if row:
            row.value = state
        else:
            db.add(AppSetting(key="content_stock_alert", value=state))
    await db.commit()


async def replenish_content(db, payload=None):
    from . import main as worker
    now = datetime.now(timezone.utc)
    policy = await current_policy(db)
    values, _, zone = await stock_config(db)
    report = await stock_report(db, now)
    # A replenisher must never override a manual mode, stop, or disabled plan.
    if policy.mode != "AUTO" or policy.emergency_stop or "threads" not in policy.platform_allowlist or not report["auto_content_plan"]:
        await record_stock_alert(db, report)
        return report
    rejected = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.APPROVED, ThreadsPost.model_qa_passed.is_(False),
        ThreadsPost.failure_reason.is_not(None),
        ThreadsPost.updated_at >= now - timedelta(days=14),
    ).limit(40))).scalars())
    for post in rejected:
        approval = (await db.execute(select(PostApproval).where(
            PostApproval.post_id == post.id, PostApproval.post_version == post.version,
        ).order_by(PostApproval.id.desc()).limit(1))).scalar_one_or_none()
        if approval is not None and approval.actor == "automation-policy":
            await remove_from_queue(db, post, "Automatic QA rejection returned to preparation")
    stale = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.SCHEDULED
    ).order_by(ThreadsPost.scheduled_at).limit(40))).scalars())
    for post in stale:
        if not await qa_is_current(db, post):
            await remove_from_queue(db, post, "The text or confirmed facts changed; preparation is required")
    report = await stock_report(db, now)
    gaps = list(report["missing_slots"])
    drafts = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.AI_GENERATED,
        ThreadsPost.created_at >= now - timedelta(days=14),
    ).order_by(ThreadsPost.model_qa_passed.desc(), ThreadsPost.failure_reason.is_not(None), ThreadsPost.created_at.desc()).limit(60))).scalars())
    scheduled = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status.in_((PostStatus.SCHEDULED, PostStatus.PUBLISHED)),
        (ThreadsPost.scheduled_at >= now - timedelta(days=1)) | (ThreadsPost.published_at >= now - timedelta(days=1)),
    ))).scalars())
    used_subjects = set()
    for item in scheduled:
        when = item.published_at if item.status == PostStatus.PUBLISHED else item.scheduled_at
        if when:
            used_subjects.add((utc(when).astimezone(zone).date(), primary_subject(item.content)))
    attempted = 0
    for post in drafts:
        if not gaps or attempted >= 6:
            break
        if "требуется проверка оператором" in (post.reasoning_summary or "").lower():
            continue
        if post.cause_type == "EXPERIMENT" and post.model_qa_at is not None and not post.model_qa_passed:
            continue
        if (post.qa_repairs or 0) >= 2 and not post.model_qa_passed:
            continue
        subject = primary_subject(post.content)
        slot = next((s for s in gaps if subject == "GENERAL" or (s.astimezone(zone).date(), subject) not in used_subjects), None)
        if slot is None:
            continue
        attempted += 1
        if await worker._auto_schedule_post(db, post, slot=slot):
            await db.commit()
            gaps.remove(slot)
            used_subjects.add((slot.astimezone(zone).date(), primary_subject(post.content)))
    # At most one batch per hour; pending/running jobs prevent a second batch.
    if gaps and report["refill_batches_today"] < report["refill_daily_limit"]:
        active = (await db.execute(select(BackgroundJob.id).where(
            BackgroundJob.kind == "content_plan",
            BackgroundJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
        ).limit(1))).scalar_one_or_none()
        hour = now.strftime("%Y%m%d%H")
        if active is None:
            language = str(values.get("content_plan_language") or values.get("default_language") or "uk")
            await enqueue(db, "content_plan", {"count": min(6, len(gaps)), "language": language, "stock_refill": True},
                          idempotency_key=f"stock-content-plan:2:{hour}")
    report = await stock_report(db, now)
    await record_stock_alert(db, report)
    return report
