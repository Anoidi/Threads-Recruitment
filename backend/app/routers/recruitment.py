"""Recruitment domain routes: agencies, vacancies, and candidates."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..enums import CandidateStage, ContactChannel, ContentType, PostStatus
from ..models import ActivityEvent, Agency, Candidate, CandidateStageEvent, PostRevision, ThreadsPost, Vacancy
from ..schemas import (
    AgencyCreate,
    AgencyRead,
    CandidateCreate,
    CandidateRead,
    GeneratePostRequest,
    PostRead,
    VacancyList,
    VacancyRead,
    VacancyUpdate,
    VacancyWrite,
)
from ..services import PostService, activity, candidate_funnel, change_candidate_stage as change_candidate_stage_service
from ..vacancy_inspection import inspection, repair_preview

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter()


class VacancyRepairRequest(BaseModel):
    source_hash: str
    fields: list[str]


class SignalConvertRequest(BaseModel):
    """A signal becomes a candidate only with a confirmed contact channel."""

    channel: ContactChannel
    contact_value: str = Field(min_length=2, max_length=255)
    confirmed_by: str = Field(default="local-operator", max_length=100)
    consent_given: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class CandidateStageRequest(BaseModel):
    stage: CandidateStage
    reason: str | None = Field(default=None, max_length=2000)
    actor: str = Field(default="local-operator", max_length=100)


@router.post("/agencies", response_model=AgencyRead, status_code=201)
async def create_agency(data: AgencyCreate, db: Db):
    agency = Agency(name=data.name)
    db.add(agency)
    await db.commit()
    await db.refresh(agency)
    return agency


@router.get("/agencies", response_model=list[AgencyRead])
async def list_agencies(db: Db):
    return list((await db.execute(select(Agency).order_by(Agency.name))).scalars())


@router.post("/vacancies", response_model=VacancyRead, status_code=201)
async def create_vacancy(data: VacancyWrite, db: Db):
    vacancy = Vacancy(**data.model_dump())
    db.add(vacancy)
    await db.flush()
    await activity(db, "vacancy_created", "vacancy", vacancy.id)
    await db.commit()
    await db.refresh(vacancy)
    return vacancy


@router.get("/vacancies", response_model=VacancyList)
async def list_vacancies(
    db: Db,
    q: str | None = None,
    active: bool | None = None,
    language: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    query = select(Vacancy)
    count = select(func.count(Vacancy.id))
    conditions = []
    if q:
        conditions.append(or_(Vacancy.title.ilike(f"%{q}%"), Vacancy.location.ilike(f"%{q}%"), Vacancy.description.ilike(f"%{q}%")))
    if active is not None:
        conditions.append(Vacancy.active == active)
    if language:
        conditions.append(Vacancy.language == language)
    if conditions:
        query = query.where(*conditions)
        count = count.where(*conditions)
    items = list((await db.execute(query.order_by(desc(Vacancy.created_at)).offset(offset).limit(limit))).scalars())
    return VacancyList(items=items, total=(await db.execute(count)).scalar_one())


@router.get("/vacancies/{vacancy_id}", response_model=VacancyRead)
async def get_vacancy(vacancy_id: int, db: Db):
    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    return item


@router.get("/vacancies/{vacancy_id}/inspection")
async def inspect_vacancy(vacancy_id: int, db: Db):
    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    peers = list((await db.execute(select(Vacancy))).scalars())
    verified = (
        await db.execute(
            select(ActivityEvent.created_at)
            .where(ActivityEvent.action == "vacancy_source_verified", ActivityEvent.entity_id == vacancy_id)
            .order_by(ActivityEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    report = inspection(item, peers, verified)
    from ..vacancy_freshness import vacancy_freshness

    report["freshness"] = vacancy_freshness(item).__dict__
    return report


@router.get("/vacancies/{vacancy_id}/repair-preview")
async def preview_vacancy_repair(vacancy_id: int, db: Db):
    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    return {"source_hash": inspection(item)["source_hash"], "changes": repair_preview(item)}


@router.get("/vacancies/{vacancy_id}/history")
async def vacancy_history(vacancy_id: int, db: Db):
    rows = (
        await db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.entity_type == "vacancy", ActivityEvent.entity_id == vacancy_id)
            .order_by(ActivityEvent.id.desc())
            .limit(50)
        )
    ).scalars()
    return [dict(action=r.action, created_at=r.created_at, detail=r.detail) for r in rows]


@router.post("/vacancies/{vacancy_id}/repair")
async def apply_vacancy_repair(vacancy_id: int, data: VacancyRepairRequest, db: Db):
    item = (await db.execute(select(Vacancy).where(Vacancy.id == vacancy_id).with_for_update())).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Vacancy not found")
    if inspection(item)["source_hash"] != data.source_hash:
        raise HTTPException(409, "Source changed; refresh preview")
    preview = {c["field"]: c for c in repair_preview(item)}
    if any(key not in preview for key in data.fields):
        raise HTTPException(422, "Select only fields from repair preview")
    changes = [preview[key] for key in dict.fromkeys(data.fields)]
    for change in changes:
        setattr(item, change["field"], change["after"])
    await activity(db, "vacancy_source_reparsed", "vacancy", item.id, {"changes": changes, "source_hash": data.source_hash})
    await db.commit()
    return inspection(item)


@router.post("/vacancies/{vacancy_id}/verify-source")
async def verify_vacancy_source(vacancy_id: int, db: Db):
    from ..vacancy_freshness import record_confirmation, vacancy_freshness

    item = await db.get(Vacancy, vacancy_id)
    if item is None:
        raise HTTPException(404, "Vacancy not found")
    record_confirmation(item)
    await activity(db, "vacancy_source_verified", "vacancy", item.id, {"source_hash": inspection(item)["source_hash"]})
    await db.commit()
    from ..queue import enqueue
    await enqueue(db, "prepare_content", {}, idempotency_key=f"prepare-source:{item.id}:{item.source_confirmed_at.isoformat()}")
    return {"verified": True, "freshness": vacancy_freshness(item).__dict__}


@router.post("/vacancies/{vacancy_id}/draft-parts")
async def vacancy_draft_parts(vacancy_id: int, db: Db):
    from ..content_quality import vacancy_fact_lines

    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    lines = vacancy_fact_lines(item)
    parts, current = [], ""
    for line in lines:
        if len(line) > 430:
            raise HTTPException(422, "One fact exceeds part limit; edit it before splitting")
        if len(current) + len(line) + 1 > 430:
            parts.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        parts.append(current)
    ids = []
    for index, text in enumerate(parts):
        post = ThreadsPost(
            vacancy_id=item.id,
            content=f"{index+1}/{len(parts)}\n{text}\nНапишіть у приватні повідомлення.",
            language=item.language,
            content_type=ContentType.VACANCY,
            status=PostStatus.DRAFT,
            reasoning_summary=f"Связанная часть {index+1}/{len(parts)} вакансии #{item.id}; публикация требует проверки полноты каждого поста.",
        )
        db.add(post)
        await db.flush()
        db.add(PostRevision(post_id=post.id, version=1, content=post.content, editor="vacancy-split"))
        ids.append(post.id)
    await activity(db, "vacancy_drafts_split", "vacancy", item.id, {"post_ids": ids})
    await db.commit()
    return {"post_ids": ids}


@router.patch("/vacancies/{vacancy_id}", response_model=VacancyRead)
async def update_vacancy(vacancy_id: int, data: VacancyUpdate, db: Db):
    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    changes = data.model_dump(exclude_unset=True)
    before = {key: getattr(item, key) for key in changes}
    for key, value in changes.items():
        setattr(item, key, value)
    await activity(db, "vacancy_updated", "vacancy", item.id, {"before": before, "after": changes})
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/vacancies/{vacancy_id}", status_code=204)
async def deactivate_vacancy(vacancy_id: int, db: Db):
    item = await db.get(Vacancy, vacancy_id)
    if not item:
        raise HTTPException(404, "Vacancy not found")
    item.active = False
    await activity(db, "vacancy_deactivated", "vacancy", item.id)
    await db.commit()
    return Response(status_code=204)


@router.post("/vacancies/{vacancy_id}/generate-post", response_model=PostRead, status_code=201)
async def generate_from_vacancy(vacancy_id: int, data: GeneratePostRequest, db: Db):
    return await PostService(db).generate(data.model_copy(update={"vacancy_id": vacancy_id}))


@router.get("/candidates", response_model=list[CandidateRead])
async def list_candidates(db: Db):
    return list((await db.execute(select(Candidate).order_by(desc(Candidate.updated_at)))).scalars())


@router.get("/candidates/funnel")
async def candidates_funnel(db: Db):
    """The confirmed funnel: signals in the queue and the candidates we reached."""
    return await candidate_funnel(db)


@router.post("/candidates", response_model=CandidateRead, status_code=201)
async def create_candidate(data: CandidateCreate, db: Db):
    candidate = Candidate(**data.model_dump())
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)
    return candidate


@router.patch("/candidates/{candidate_id}/stage", response_model=CandidateRead)
async def change_candidate_stage(candidate_id: int, data: CandidateStageRequest, db: Db):
    """Move a candidate along the funnel. Every step is a manual decision."""
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    return await change_candidate_stage_service(db, candidate, data.stage.value, actor=data.actor, reason=data.reason)


@router.get("/candidates/{candidate_id}/history")
async def candidate_history(candidate_id: int, db: Db):
    rows = list(
        (
            await db.execute(
                select(CandidateStageEvent)
                .where(CandidateStageEvent.candidate_id == candidate_id)
                .order_by(CandidateStageEvent.id.desc())
            )
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "from_stage": row.from_stage,
            "to_stage": row.to_stage,
            "actor": row.actor,
            "reason": row.reason,
            "created_at": row.created_at,
        }
        for row in rows
    ]
