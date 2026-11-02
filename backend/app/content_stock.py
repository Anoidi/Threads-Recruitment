"""Read-only stock and slot report shared by the dashboard and replenisher."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select, func
from .config import settings
from .enums import PostStatus
from .models import AppSetting, BackgroundJob, ThreadsPost
from .model_qa import qa_is_current

SLOTS = {
    1: ((12, 0),), 2: ((9, 0), (17, 30)), 3: ((9, 0), (13, 0), (18, 30)),
    4: ((8, 30), (12, 0), (16, 0), (20, 0)),
    5: ((8, 0), (11, 0), (14, 0), (17, 0), (20, 0)),
    6: ((8, 0), (10, 30), (13, 0), (15, 30), (18, 0), (20, 30)),
}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def enabled(value):
    return value is True or str(value).lower() in {"true", "1", "yes", "on"}


async def stock_config(db):
    values = {r.key: r.value for r in (await db.execute(select(AppSetting).where(
        AppSetting.key.in_(("timezone", "posts_per_day", "auto_content_plan", "content_plan_language", "default_language"))
    ))).scalars()}
    try:
        per_day = max(1, min(6, int(values.get("posts_per_day") or 3)))
    except (ValueError, TypeError):
        per_day = 3
    try:
        zone = ZoneInfo(str(values.get("timezone") or settings.timezone))
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Warsaw")
    return values, per_day, zone


def upcoming_slots(now, per_day, zone, days=3):
    local = now.astimezone(zone)
    slots = []
    for offset in range(days + 1):
        day = local.date() + timedelta(days=offset)
        for hour, minute in SLOTS[per_day]:
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
            if now + timedelta(minutes=10) < slot <= now + timedelta(days=days):
                slots.append(slot)
    return slots


async def stock_report(db, now=None):
    now = utc(now or datetime.now(timezone.utc))
    values, per_day, zone = await stock_config(db)
    slots = upcoming_slots(now, per_day, zone)
    posts = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status.in_((PostStatus.SCHEDULED, PostStatus.AI_GENERATED, PostStatus.APPROVED)),
    ).order_by(ThreadsPost.created_at.desc()))).scalars())
    ready, reserve, stale, rejected = [], [], [], []
    for post in posts:
        if post.status == PostStatus.SCHEDULED:
            if await qa_is_current(db, post):
                ready.append(post)
            else:
                stale.append(post.id)
        elif post.model_qa_passed and await qa_is_current(db, post):
            reserve.append(post.id)
        elif post.failure_reason or (post.model_qa_at is not None and not post.model_qa_passed):
            rejected.append({"id": post.id, "reason": post.model_qa_notes or post.failure_reason or "Проверка не пройдена",
                             "repairs": post.qa_repairs or 0})
    times = sorted(utc(p.scheduled_at) for p in ready if p.scheduled_at and utc(p.scheduled_at) > now)
    # Consume each post once: one publication cannot cover two nearby slots.
    remaining = list(times)
    gaps = []
    covered = 0
    for slot in slots:
        match = next((t for t in remaining if abs((t-slot).total_seconds()) <= 1200), None)
        if match is None:
            gaps.append(slot)
        else:
            covered += 1
            remaining.remove(match)
    day_start = datetime.combine(now.astimezone(zone).date(), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    refill_batches = (await db.execute(select(func.count(BackgroundJob.id)).where(
        BackgroundJob.kind == "content_plan", BackgroundJob.idempotency_key.like("stock-content-plan:%"),
        BackgroundJob.created_at >= day_start,
    ))).scalar_one()
    horizon = now + timedelta(days=3)
    queued = sum(t <= horizon for t in times)
    return {
        "refill_batches_today": refill_batches, "refill_daily_limit": 4,
        "target_days": 3, "posts_per_day": per_day, "ready_scheduled": queued,
        "ready_reserve": len(reserve), "coverage_days": round((queued + len(reserve)) / per_day, 1),
        "next_publication_at": times[0] if times else None,
        "missing_slots": gaps, "covered_slots": covered, "stale_scheduled_ids": stale,
        "rejected_count": len(rejected), "rejected_posts": rejected[:8],
        "auto_content_plan": enabled(values.get("auto_content_plan", True)),
        "timezone": str(zone),
    }
