"""Content lifecycle routes: posts, calendar, editorial memory, topics, and format experiments."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import AIService
from ..config import settings
from ..database import get_db
from ..editorial_memory import memory
from ..enums import MetricsStatus, PostStatus
from ..models import AppSetting, ThreadExperiment, ThreadsPost, Topic
from ..queue import enqueue
from ..schemas import (
    DecisionRequest,
    GeneratePostRequest,
    GenerateProvocativeRequest,
    PostCreate,
    PostList,
    PostRead,
    PostUpdate,
    ProvocativeVariantRead,
    RewriteRequest,
    ScheduleRequest,
    TopicRead,
    TopicUpdate,
)
from ..security import require_local_operator
from ..services import PostService, activity

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter()


class EditorialDecision(BaseModel):
    status: Literal["CONFIRMED", "REJECTED", "DISABLED"]


@router.post("/threads/posts/generate", response_model=PostRead, status_code=201)
async def generate_post(data: GeneratePostRequest, db: Db):
    return await PostService(db).generate(data)


@router.post("/threads/posts/generate-provocative", response_model=list[ProvocativeVariantRead], status_code=201)
async def generate_provocative_posts(data: GenerateProvocativeRequest, db: Db):
    """Create three Ukrainian provocative drafts; no variant is published."""
    return await PostService(db).generate_provocative(data.vacancy_id, data.cta)


@router.post("/threads/posts", response_model=PostRead, status_code=201)
async def create_post(data: PostCreate, db: Db):
    return await PostService(db).create(data)


@router.get("/threads/posts", response_model=PostList)
async def list_posts(
    db: Db,
    status_filter: PostStatus | None = Query(None, alias="status"),
    vacancy_id: int | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    conditions = []
    if status_filter:
        conditions.append(ThreadsPost.status == status_filter)
    if vacancy_id:
        conditions.append(ThreadsPost.vacancy_id == vacancy_id)
    query = select(ThreadsPost).where(*conditions).order_by(desc(ThreadsPost.created_at)).offset(offset).limit(limit)
    count = select(func.count(ThreadsPost.id)).where(*conditions)
    return PostList(items=list((await db.execute(query)).scalars()), total=(await db.execute(count)).scalar_one())


@router.get("/threads/posts/{post_id}", response_model=PostRead)
async def get_post(post_id: int, db: Db):
    return await PostService(db).get(post_id)


@router.patch("/threads/posts/{post_id}", response_model=PostRead)
async def update_post(post_id: int, data: PostUpdate, db: Db):
    return await PostService(db).update(post_id, data)


@router.post("/threads/posts/{post_id}/rewrite", response_model=PostRead)
async def rewrite_post(post_id: int, data: RewriteRequest, db: Db):
    service = PostService(db)
    post = await service.get(post_id)
    rewritten = await AIService().rewrite(post.content, data.operation, data.language or post.language)
    return await service.update(post_id, PostUpdate(content=rewritten, language=data.language), editor="writer-agent")


@router.get("/threads/editorial-memory")
async def get_editorial_memory(db: Db):
    return await memory(db)


@router.put("/threads/editorial-memory/{rule_id}")
async def decide_editorial_memory(rule_id: str, data: EditorialDecision, request: Request, db: Db):
    require_local_operator(request)
    if not any(item["id"] == rule_id for item in await memory(db)):
        raise HTTPException(404, "Editorial preference not found")
    row = (await db.execute(select(AppSetting).where(AppSetting.key == "editorial_memory_decisions").with_for_update())).scalar_one_or_none()
    if row is None:
        row = AppSetting(key="editorial_memory_decisions", value={})
        db.add(row)
    row.value = {**(row.value or {}), rule_id: data.status}
    await activity(db, "editorial_memory_decision", detail={"rule_id": rule_id, "status": data.status})
    await db.commit()
    return {"id": rule_id, "status": data.status}


@router.post("/threads/posts/{post_id}/review", response_model=PostRead)
async def submit_review(post_id: int, db: Db):
    return await PostService(db).submit(post_id)


@router.post("/threads/posts/{post_id}/approve", response_model=PostRead)
async def approve_post(post_id: int, data: DecisionRequest, db: Db):
    return await PostService(db).approve(post_id, data.expected_version, data.note)


@router.post("/threads/posts/{post_id}/reject", response_model=PostRead)
async def reject_post(post_id: int, data: DecisionRequest, db: Db):
    return await PostService(db).reject(post_id, data.expected_version, data.note)


@router.post("/threads/posts/{post_id}/schedule", response_model=PostRead)
async def schedule_post(post_id: int, data: ScheduleRequest, db: Db):
    return await PostService(db).schedule(post_id, data)


@router.post("/threads/posts/{post_id}/cancel-schedule", response_model=PostRead)
async def cancel_schedule(post_id: int, db: Db):
    return await PostService(db).cancel_schedule(post_id)


@router.post("/threads/posts/{post_id}/publish", response_model=PostRead, status_code=202)
async def publish_post(post_id: int, db: Db, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
    return await PostService(db).request_publish(post_id, idempotency_key)


@router.post("/threads/posts/{post_id}/duplicate", response_model=PostRead, status_code=201)
async def duplicate_post(post_id: int, db: Db):
    return await PostService(db).duplicate(post_id)


@router.post("/threads/posts/{post_id}/archive", response_model=PostRead)
async def archive_post(post_id: int, db: Db):
    return await PostService(db).archive(post_id)


@router.post("/threads/posts/{post_id}/metrics/retry", response_model=PostRead)
async def retry_post_metrics(post_id: int, db: Db):
    """Re-queue metric collection for a post marked as unavailable."""
    post = await PostService(db).get(post_id)
    if not post.remote_id or post.remote_id.startswith("mock-"):
        raise HTTPException(409, "Post has no measurable Threads media id")
    post.metrics_status = MetricsStatus.PENDING
    post.metrics_note = None
    await enqueue(
        db,
        "collect_metrics",
        {"post_id": post.id},
        idempotency_key=f"collect-metrics-manual:{post.id}:{int(datetime.now(timezone.utc).timestamp())}",
    )
    await activity(db, "post_metrics_retry_requested", "post", post.id)
    await db.commit()
    await db.refresh(post)
    return post


@router.get("/threads/calendar", response_model=list[PostRead])
async def calendar(db: Db, start: datetime | None = None, end: datetime | None = None):
    query = select(ThreadsPost).where(ThreadsPost.status.in_([PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.PUBLISHED, PostStatus.FAILED]))
    if start:
        query = query.where(or_(ThreadsPost.scheduled_at >= start, ThreadsPost.published_at >= start))
    if end:
        query = query.where(or_(ThreadsPost.scheduled_at <= end, ThreadsPost.published_at <= end))
    return list((await db.execute(query.order_by(ThreadsPost.scheduled_at, ThreadsPost.created_at))).scalars())


@router.post("/threads/content-plan", status_code=202)
async def generate_content_plan(
    db: Db,
    count: int = Query(3, ge=1, le=6),
    language: Literal["uk", "ru", "pl", "en"] | None = None,
):
    """Queue an audience-content plan."""
    payload = {"count": count, "language": language or settings.default_language}
    key = f"content-plan:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
    job = await enqueue(db, "content_plan", payload, idempotency_key=key)
    return {"job_id": job.id, "status": job.status, "language": payload["language"], "count": count}


@router.get("/threads/topics", response_model=list[TopicRead])
async def list_topics(db: Db):
    return list((await db.execute(select(Topic).order_by(desc(Topic.priority), desc(Topic.created_at)))).scalars())


@router.patch("/threads/topics/{topic_id}", response_model=TopicRead)
async def update_topic(topic_id: int, data: TopicUpdate, db: Db):
    topic = await db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    topic.status = data.status
    await db.commit()
    await db.refresh(topic)
    return topic


@router.get("/threads/experiments")
async def list_experiments(db: Db):
    """The format experiments and what they really show."""
    from ..experiments import current_report, experiment_report

    rows = list((await db.execute(select(ThreadExperiment).order_by(desc(ThreadExperiment.id)).limit(20))).scalars())
    items = []
    for experiment in rows:
        if experiment.status == "CONCLUDED" and experiment.report:
            items.append(experiment.report)
        else:
            items.append(await experiment_report(db, experiment))
    return {"experiments": items, "current": await current_report(db)}


@router.post("/threads/experiments", status_code=201)
async def start_format_experiment(payload: dict, db: Db):
    """Open a format experiment. It creates drafts later, it never publishes."""
    from ..experiments import experiment_report, start_experiment

    subject = str(payload.get("subject") or "").strip().upper() or None
    formats = payload.get("formats")
    try:
        experiment = await start_experiment(
            db,
            subject=subject,
            formats=[str(item).strip().upper() for item in formats] if isinstance(formats, list) else None,
            name=str(payload.get("name") or "")[:160] or None,
            hypothesis=str(payload.get("hypothesis") or "")[:1000] or None,
            created_by=str(payload.get("actor") or "local-operator")[:100],
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await activity(
        db,
        "experiment_started",
        "experiment",
        experiment.id,
        {
            "subject": experiment.subject,
            "fact": experiment.fact,
            "variants": [item.get("key") for item in experiment.variants or []],
            "actor": experiment.created_by,
        },
    )
    await db.commit()
    return await experiment_report(db, experiment)


@router.post("/threads/experiments/{experiment_id}/conclude")
async def conclude_format_experiment(experiment_id: int, db: Db):
    """Freeze the result of an experiment and keep the verdict."""
    from ..experiments import conclude_experiment

    experiment = await db.get(ThreadExperiment, experiment_id)
    if not experiment:
        raise HTTPException(404, "Experiment not found")
    if experiment.status == "CONCLUDED" and experiment.report:
        return experiment.report
    report = await conclude_experiment(db, experiment)
    await activity(
        db,
        "experiment_concluded",
        "experiment",
        experiment.id,
        {"verdict": report.get("verdict"), "winner": (report.get("winner") or {}).get("key")},
    )
    await db.commit()
    return report
