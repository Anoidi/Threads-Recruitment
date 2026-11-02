from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .ai import AIService
from .comment_engagement import normalize_username
from .editorial_memory import approved_rules, redact
from .enums import ALLOWED_STAGE_TRANSITIONS, CandidateSignalStatus, CandidateStage, ContentType, JobStatus, PostStatus
from .models import ActivityEvent, AppSetting, BackgroundJob, Candidate, CandidateSignal, CandidateStageEvent, PostApproval, PostRevision, ThreadsPost, Vacancy
from .queue import enqueue
from .post_validation import validate_post_for_publication
from .content_context import account_contact
from .schemas import GeneratePostRequest, PostCreate, PostUpdate, ScheduleRequest

ALLOWED_TRANSITIONS: dict[PostStatus, set[PostStatus]] = {
    PostStatus.DRAFT: {PostStatus.REVIEW, PostStatus.ARCHIVED},
    PostStatus.AI_GENERATED: {PostStatus.REVIEW, PostStatus.ARCHIVED},
    PostStatus.REVIEW: {PostStatus.APPROVED, PostStatus.DRAFT, PostStatus.ARCHIVED},
    PostStatus.APPROVED: {PostStatus.SCHEDULED, PostStatus.PUBLISHING, PostStatus.FAILED, PostStatus.ARCHIVED},
    PostStatus.SCHEDULED: {PostStatus.APPROVED, PostStatus.PUBLISHING, PostStatus.FAILED, PostStatus.ARCHIVED},
    PostStatus.PUBLISHING: {PostStatus.PUBLISHED, PostStatus.FAILED},
    PostStatus.FAILED: {PostStatus.APPROVED, PostStatus.ARCHIVED},
    PostStatus.PUBLISHED: {PostStatus.ARCHIVED},
    PostStatus.ARCHIVED: set(),
}


async def activity(db: AsyncSession, action: str, entity_type: str | None = None, entity_id: int | None = None, detail: dict | None = None) -> None:
    db.add(ActivityEvent(category="threads", action=action, entity_type=entity_type, entity_id=entity_id, detail=detail or {}))


class PostService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, post_id: int) -> ThreadsPost:
        post = await self.db.get(ThreadsPost, post_id)
        if not post:
            raise HTTPException(404, "Post not found")
        return post

    async def get_for_update(self, post_id: int) -> ThreadsPost:
        post = (await self.db.execute(select(ThreadsPost).where(ThreadsPost.id == post_id).with_for_update())).scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        return post

    async def create(self, data: PostCreate) -> ThreadsPost:
        if data.idempotency_key:
            existing = (await self.db.execute(select(ThreadsPost).where(ThreadsPost.idempotency_key == data.idempotency_key))).scalar_one_or_none()
            if existing:
                return existing
        post = ThreadsPost(**data.model_dump())
        from .content_subjects import primary_subject
        post.content_subject = primary_subject(post.content or "")
        self.db.add(post)
        await self.db.flush()
        self.db.add(PostRevision(post_id=post.id, version=1, content=post.content))
        await activity(self.db, "post_created", "post", post.id)
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def generate(self, data: GeneratePostRequest) -> ThreadsPost:
        vacancy = await self.db.get(Vacancy, data.vacancy_id) if data.vacancy_id is not None else None
        if data.vacancy_id is not None and not vacancy:
            raise HTTPException(404, "Vacancy not found")
        phone = (await self.db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))).scalar_one_or_none()
        requested_cta = data.cta
        if vacancy is not None and phone and not requested_cta:
            requested_cta = f"☎️ {phone}"
        ai = AIService()
        ai.editorial_rules = await approved_rules(self.db, data.language, data.content_type)
        result = await ai.generate_post(vacancy, data.content_type, data.language, data.tone, requested_cta, data.idea)
        post = ThreadsPost(
            vacancy_id=vacancy.id if vacancy is not None else None, hook=result["hook"], content=result["content"], content_type=data.content_type,
            language=data.language, reasoning_summary=result.get("reasoning_summary"), status=PostStatus.AI_GENERATED,
        )
        self.db.add(post)
        await self.db.flush()
        self.db.add(PostRevision(post_id=post.id, version=1, content=post.content, editor="writer-agent"))
        await activity(self.db, "post_ai_generated", "post", post.id, {"vacancy_id": vacancy.id if vacancy is not None else None})
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def generate_provocative(self, vacancy_id: int, cta: str | None = None) -> list[dict]:
        vacancy = await self.db.get(Vacancy, vacancy_id)
        if not vacancy:
            raise HTTPException(404, "Vacancy not found")
        variants = await AIService().generate_provocative_variants(vacancy, cta)
        created: list[dict] = []
        for variant in variants:
            post = ThreadsPost(
                vacancy_id=vacancy.id,
                hook=variant["content"].split(".", 1)[0][:500],
                content=variant["content"],
                content_type=ContentType.PROVOCATIVE_RECRUITMENT,
                language="uk", status=PostStatus.AI_GENERATED,
                reasoning_summary=f"Провокаційний варіант: {variant['style']}; факти взяті зі структурованої вакансії.",
            )
            self.db.add(post)
            await self.db.flush()
            self.db.add(PostRevision(post_id=post.id, version=1, content=post.content, editor="writer-agent"))
            await activity(self.db, "provocative_variant_generated", "post", post.id, {"style": variant["style"], "vacancy_id": vacancy.id})
            created.append({**variant, "post_id": post.id})
        await self.db.commit()
        return created

    async def update(self, post_id: int, data: PostUpdate, editor: str = "human") -> ThreadsPost:
        post = await self.get_for_update(post_id)
        if post.status not in {PostStatus.DRAFT, PostStatus.AI_GENERATED, PostStatus.REVIEW, PostStatus.FAILED}:
            raise HTTPException(409, f"Cannot edit a {post.status} post")
        changes = data.model_dump(exclude_unset=True)
        category = changes.pop('feedback_category', None)
        reason = changes.pop('feedback_reason', None)
        expected = changes.pop('expected_version', None)
        if expected is not None and expected != post.version:
            raise HTTPException(409, 'Post changed; reload before editing')
        if {"content", "language", "vacancy_id", "content_type"} & changes.keys():
            post.model_qa_passed = False
            post.model_qa_at = None
            post.model_qa_notes = None
            post.model_qa_input_hash = None
            post.qa_repairs = 0
        before, old_version = post.content, post.version
        for key, value in changes.items():
            setattr(post, key, value)
        if "content" in changes:
            from .content_subjects import primary_subject
            post.content_subject = primary_subject(post.content or "")
            post.version += 1
            self.db.add(PostRevision(post_id=post.id, version=post.version, content=post.content, editor=editor))
            if editor == 'human' and before != post.content:
                await activity(self.db, 'post_editorial_feedback', 'post', post.id, {
                    'before': redact(before), 'after': redact(post.content), 'from_version': old_version, 'to_version': post.version,
                    'language': post.language, 'content_type': post.content_type.value, 'category': category or 'other',
                    'reason': redact(reason), 'reason_known': bool(reason),
                })
        await activity(self.db, "post_edited", "post", post.id)
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def transition(self, post: ThreadsPost, target: PostStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[post.status]:
            raise HTTPException(409, f"Invalid post transition: {post.status} -> {target}")
        post.status = target

    async def submit(self, post_id: int) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        await self.transition(post, PostStatus.REVIEW)
        await activity(self.db, "post_submitted_for_review", "post", post.id)
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def approve(self, post_id: int, expected_version: int, note: str | None) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        if post.version != expected_version:
            raise HTTPException(409, "Post changed after review; reload before approving")
        await self.transition(post, PostStatus.APPROVED)
        self.db.add(PostApproval(post_id=post.id, post_version=post.version, decision="APPROVED", actor="local-operator", note=note))
        await activity(self.db, "post_approved", "post", post.id, {"actor": "local-operator", "version": post.version})
        # An explicit operator approval is still required. The autonomy policy
        # decides whether approved content may be scheduled without a second
        # click. The old auto_post flag is no longer consulted: the policy is
        # the single source of truth.
        from .autonomy import ACTION_SCHEDULE, evaluate_and_record

        decision = await evaluate_and_record(
            self.db, action=ACTION_SCHEDULE, subject_type="post", subject_id=post.id,
            context={"approved": True},
        )
        await self.db.commit()
        from .model_qa import prepare_post_qa

        if decision.allowed and await prepare_post_qa(self.db, post):
            run_at = datetime.now(timezone.utc) + timedelta(minutes=1)
            await self.transition(post, PostStatus.SCHEDULED)
            post.scheduled_at = run_at
            key = f"publish-post:{post.id}:v{post.version}:approved-auto"
            await enqueue(
                self.db,
                "publish_post",
                {"post_id": post.id, "expected_status": PostStatus.SCHEDULED.value, "expected_version": post.version},
                run_at=run_at,
                idempotency_key=key,
            )
            await activity(self.db, "post_auto_scheduled_after_approval", "post", post.id, {"run_at": run_at.isoformat(), "policy_mode": decision.mode})
            await self.db.refresh(post)
            return post
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def reject(self, post_id: int, expected_version: int, note: str | None) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        if post.version != expected_version:
            raise HTTPException(409, "Post changed after review; reload before rejecting")
        await self.transition(post, PostStatus.DRAFT)
        self.db.add(PostApproval(post_id=post.id, post_version=post.version, decision="REJECTED", actor="local-operator", note=note))
        await activity(self.db, "post_rejected", "post", post.id, {"actor": "local-operator", "version": post.version})
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def schedule(self, post_id: int, data: ScheduleRequest) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        scheduled_at = data.scheduled_at.astimezone(timezone.utc)
        if scheduled_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "scheduled_at must be in the future")
        if post.status not in {PostStatus.APPROVED, PostStatus.SCHEDULED}:
            raise HTTPException(409, f"Cannot schedule a {post.status} post")
        from .model_qa import prepare_post_qa

        if not await prepare_post_qa(self.db, post):
            await self.db.commit()
            raise HTTPException(409, "Quality review failed before scheduling: " + (post.model_qa_notes or ""))
        if post.status == PostStatus.APPROVED:
            await self.transition(post, PostStatus.SCHEDULED)
        elif post.status != PostStatus.SCHEDULED:
            raise HTTPException(409, f"Cannot schedule a {post.status} post")
        post.scheduled_at = scheduled_at
        key = f"publish-post:{post.id}:v{post.version}:scheduled:{scheduled_at.isoformat()}"

        jobs = list((await self.db.execute(select(BackgroundJob).where(
            BackgroundJob.kind == "publish_post",
            BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        ).with_for_update())).scalars())
        matching = [job for job in jobs if int(job.payload.get("post_id", -1)) == post.id]
        if any(job.status == JobStatus.RUNNING for job in matching):
            raise HTTPException(409, "Publication is already in progress and cannot be rescheduled")
        for job in matching:
            if job.idempotency_key != key:
                job.status = JobStatus.FAILED
                job.last_error = "Superseded by reschedule"

        await activity(
            self.db,
            "post_rescheduled" if matching else "post_scheduled",
            "post",
            post.id,
            {"scheduled_at": scheduled_at.isoformat()},
        )
        # enqueue commits the post transition, activity event, and durable job together.
        await enqueue(self.db, "publish_post", {"post_id": post.id, "expected_status": PostStatus.SCHEDULED.value, "expected_version": post.version}, run_at=scheduled_at, idempotency_key=key)
        await self.db.refresh(post)
        return post

    async def cancel_schedule(self, post_id: int) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        jobs = list((await self.db.execute(select(BackgroundJob).where(BackgroundJob.kind == "publish_post", BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])).with_for_update())).scalars())
        matching = [job for job in jobs if int(job.payload.get("post_id", -1)) == post.id]
        if any(job.status == JobStatus.RUNNING for job in matching):
            raise HTTPException(409, "Publication is already in progress and cannot be cancelled")
        await self.transition(post, PostStatus.APPROVED)
        post.scheduled_at = None
        for job in matching:
            job.status = JobStatus.FAILED
            job.last_error = "Cancelled by operator"
        await activity(self.db, "post_schedule_cancelled", "post", post.id)
        await self.db.commit()
        await self.db.refresh(post)
        return post

    async def request_publish(self, post_id: int, idempotency_key: str | None = None) -> ThreadsPost:
        post = await self.get_for_update(post_id)
        if post.status not in {PostStatus.APPROVED, PostStatus.SCHEDULED}:
            raise HTTPException(409, "Сначала отправьте текст на проверку и одобрите его")
        vacancy = await self.db.get(Vacancy, post.vacancy_id) if post.vacancy_id is not None else None
        validate_post_for_publication(content=post.content, language=post.language, vacancy=vacancy,
                                      content_type=post.content_type, account_contact=await account_contact(self.db))
        jobs = list((await self.db.execute(select(BackgroundJob).where(
            BackgroundJob.kind == "publish_post",
            BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        ).with_for_update())).scalars())
        matching = [job for job in jobs if int(job.payload.get("post_id", -1)) == post.id]
        if any(job.status == JobStatus.RUNNING for job in matching):
            raise HTTPException(409, "Публикация уже выполняется. Дождитесь результата")
        # Explicit human publication is governed by the operator policy in the
        # worker. Model QA is a gate for automatic publication, not a veto on
        # the operator's decision. Deterministic validation remains mandatory.
        key = f"publish-post:{post.id}:v{post.version}:manual"
        existing = (await self.db.execute(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))).scalar_one_or_none()
        if existing and existing.status == JobStatus.FAILED:
            key += f":retry:{uuid4()}"
        for job in matching:
            if job.idempotency_key != key:
                job.status = JobStatus.FAILED
                job.last_error = "Replaced by explicit publish-now request"
        post.status = PostStatus.APPROVED
        post.scheduled_at = None
        post.failure_reason = None
        await activity(self.db, "post_manual_publish_requested", "post", post.id, {"version": post.version})
        await enqueue(self.db, "publish_post", {"post_id": post.id, "expected_status": PostStatus.APPROVED.value,
                      "expected_version": post.version, "human_initiated": True}, idempotency_key=key)
        return post

    async def duplicate(self, post_id: int) -> ThreadsPost:
        source = await self.get(post_id)
        return await self.create(PostCreate(vacancy_id=source.vacancy_id, content=source.content, hook=source.hook, content_type=source.content_type, language=source.language))

    async def archive(self, post_id: int) -> ThreadsPost:
        post = await self.get(post_id)
        await self.transition(post, PostStatus.ARCHIVED)
        await activity(self.db, "post_archived", "post", post.id)
        await self.db.commit()
        await self.db.refresh(post)
        return post


async def convert_signal(db: AsyncSession, signal: CandidateSignal, *, channel: str, contact_value: str, confirmed_by: str = "local-operator", consent_given: bool = False, notes: str | None = None) -> Candidate:
    """Turn a signal into a candidate, but only with a confirmed contact.

    The contact channel is required on purpose: a candidate record without a
    way to reach the person is a guess, and a guess must not be counted as a
    result.
    """
    signal = (await db.execute(select(CandidateSignal).where(CandidateSignal.id == signal.id).with_for_update())).scalar_one()
    if signal.candidate_id:
        candidate = await db.get(Candidate, signal.candidate_id)
        if candidate:
            return candidate
    now = datetime.now(timezone.utc)
    # A person is one candidate. Before this check a reader who wrote twice
    # produced two candidate records, which doubled the funnel and made every
    # per-post candidate count unreliable.
    handle = normalize_username(signal.username)
    existing = None
    if handle:
        rows = list((await db.execute(
            select(Candidate).where(Candidate.threads_username.is_not(None)).order_by(Candidate.id)
        )).scalars())
        existing = next(
            (item for item in rows if normalize_username(item.threads_username) == handle),
            None,
        )
    if existing is not None:
        signal.candidate_id = existing.id
        signal.status = CandidateSignalStatus.CONVERTED
        if existing.vacancy_id is None and signal.vacancy_id is not None:
            existing.vacancy_id = signal.vacancy_id
        await activity(db, "candidate_signal_linked", "candidate_signal", signal.id, {
            "candidate_id": existing.id, "channel": channel,
        })
        await db.commit()
        await db.refresh(existing)
        return existing
    candidate = Candidate(
        source_signal_id=signal.id,
        vacancy_id=signal.vacancy_id,
        name=signal.username,
        threads_username=signal.username,
        contact=contact_value,
        notes=notes or f"Converted from candidate signal #{signal.id}",
        status=CandidateStage.NEW.value,
        channel=channel,
        contact_value=contact_value,
        confirmed_by=confirmed_by,
        confirmed_at=now,
        consent_given=consent_given,
        consent_at=now if consent_given else None,
    )
    db.add(candidate)
    await db.flush()
    signal.candidate_id = candidate.id
    signal.status = CandidateSignalStatus.CONVERTED
    db.add(CandidateStageEvent(
        candidate_id=candidate.id, from_stage=None, to_stage=CandidateStage.NEW.value,
        actor=confirmed_by, reason=f"Contact confirmed via {channel}.",
    ))
    await activity(db, "candidate_signal_converted", "candidate_signal", signal.id, {
        "candidate_id": candidate.id, "channel": channel, "confirmed_by": confirmed_by,
    })
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(Candidate).where(Candidate.source_signal_id == signal.id))).scalar_one()
        return existing
    await db.refresh(candidate)
    return candidate


async def change_candidate_stage(db: AsyncSession, candidate: Candidate, stage: str, *, actor: str = "local-operator", reason: str | None = None) -> Candidate:
    """Move a candidate to the next funnel stage and record the decision."""
    target = str(stage)
    current = candidate.status or CandidateStage.NEW.value
    allowed = ALLOWED_STAGE_TRANSITIONS.get(CandidateStage(current).value if current in {item.value for item in CandidateStage} else CandidateStage.NEW.value, set())
    if target not in {item.value for item in allowed}:
        raise HTTPException(409, f"Cannot move a candidate from {current} to {target}")
    if target in {CandidateStage.HIRED.value, CandidateStage.REJECTED.value} and not (reason or "").strip():
        raise HTTPException(422, "A reason is required for a hiring or rejection decision")
    candidate.status = target
    db.add(CandidateStageEvent(
        candidate_id=candidate.id, from_stage=current, to_stage=target, actor=actor, reason=(reason or None),
    ))
    await activity(db, "candidate_stage_changed", "candidate", candidate.id, {
        "from": current, "to": target, "actor": actor, "reason": (reason or "")[:200],
    })
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def candidate_funnel(db: AsyncSession) -> dict:
    """The confirmed recruitment funnel, not the raw signal count."""
    signals = dict((await db.execute(
        select(CandidateSignal.status, func.count(CandidateSignal.id)).group_by(CandidateSignal.status)
    )).all())
    stages = dict((await db.execute(
        select(Candidate.status, func.count(Candidate.id)).group_by(Candidate.status)
    )).all())
    with_contact = (await db.execute(
        select(func.count(Candidate.id)).where(Candidate.confirmed_at.is_not(None))
    )).scalar_one()
    consented = (await db.execute(
        select(func.count(Candidate.id)).where(Candidate.consent_given.is_(True))
    )).scalar_one()
    return {
        "signals_total": sum(signals.values()),
        "signals_new": int(signals.get(CandidateSignalStatus.NEW.value, 0)),
        "signals_reviewed": int(signals.get(CandidateSignalStatus.REVIEWED.value, 0)),
        "signals_ignored": int(signals.get(CandidateSignalStatus.IGNORED.value, 0)),
        "signals_converted": int(signals.get(CandidateSignalStatus.CONVERTED.value, 0)),
        "candidates_total": sum(stages.values()),
        "contacts_confirmed": int(with_contact),
        "consent_recorded": int(consented),
        "stage_new": int(stages.get(CandidateStage.NEW.value, 0)),
        "stage_contacted": int(stages.get(CandidateStage.CONTACTED.value, 0)),
        "stage_qualified": int(stages.get(CandidateStage.QUALIFIED.value, 0)),
        "stage_hired": int(stages.get(CandidateStage.HIRED.value, 0)),
        "stage_rejected": int(stages.get(CandidateStage.REJECTED.value, 0)),
    }

