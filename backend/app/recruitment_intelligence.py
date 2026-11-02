"""Recruitment intelligence: where the candidates are not coming from.

The content engine optimises reach. The business is candidates. This module
closes that distance with the data the system already has: every candidate
signal points at an interaction, every interaction points at a post, and every
post points at a vacancy.

The result is a demand deficit: an active vacancy that nobody asks about.
A deficit is the most actionable signal in the system, because it names the
exact job the content should talk about next.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from .comment_engagement import normalize_username
from .enums import CandidateSignalStatus, PostStatus
from .models import Candidate, CandidateSignal, ThreadsInteraction, ThreadsPost, Vacancy


async def vacancy_demand(db) -> dict:
    """Which active vacancies attract nobody, and which attract the most."""
    vacancies = list((await db.execute(
        select(Vacancy).where(Vacancy.active.is_(True)).order_by(Vacancy.created_at.desc())
    )).scalars())
    if not vacancies:
        return {"vacancies": [], "deficits": [], "top": None, "summary": "Нет активных вакансий."}

    # Aggregated in SQL (audit I-8/D7): this used to load every post, every
    # interaction and every signal into Python. The join chain is the same
    # rule: a signal counts only when neither it nor its interaction was
    # ignored, and a candidate is one person even when they wrote twice.
    published_per_vacancy: dict[int, int] = {
        int(vacancy_id): int(count) for vacancy_id, count in (await db.execute(
            select(ThreadsPost.vacancy_id, func.count(ThreadsPost.id))
            .where(ThreadsPost.vacancy_id.is_not(None), ThreadsPost.status == PostStatus.PUBLISHED)
            .group_by(ThreadsPost.vacancy_id)
        )).all()
    }
    interest_per_vacancy = {
        int(row["vacancy_id"]): (int(row["signals"]), int(row["candidates"]))
        for row in (await db.execute(
            select(
                ThreadsPost.vacancy_id.label("vacancy_id"),
                func.count(CandidateSignal.id).label("signals"),
                func.count(func.distinct(CandidateSignal.candidate_id)).label("candidates"),
            )
            .join(ThreadsInteraction, ThreadsInteraction.id == CandidateSignal.interaction_id)
            .join(ThreadsPost, ThreadsPost.id == ThreadsInteraction.post_id)
            .where(
                CandidateSignal.status != CandidateSignalStatus.IGNORED,
                ThreadsInteraction.ignored.is_(False),
                ThreadsPost.vacancy_id.is_not(None),
            )
            .group_by(ThreadsPost.vacancy_id)
        )).mappings().all()
    }

    rows = []
    for vacancy in vacancies:
        signals, candidates = interest_per_vacancy.get(vacancy.id, (0, 0))
        rows.append({
            "vacancy_id": vacancy.id,
            "title": vacancy.title,
            "signals": signals,
            "candidates": candidates,
            "published_posts": published_per_vacancy.get(vacancy.id, 0),
        })
    deficits = [
        row for row in rows
        if row["signals"] == 0 and row["published_posts"] <= 1
    ]
    deficits.sort(key=lambda row: (row["published_posts"], row["vacancy_id"]))
    top = max(rows, key=lambda row: (row["candidates"], row["signals"]), default=None)
    return {
        "vacancies": sorted(rows, key=lambda row: -row["signals"]),
        "deficits": deficits,
        "top": top,
        "summary": _summary(rows, deficits, top),
    }


def _summary(rows: list[dict], deficits: list[dict], top: dict | None) -> str:
    if not rows:
        return "Нет активных вакансий."
    with_interest = [row for row in rows if row["signals"] > 0]
    parts = [f"активных вакансий: {len(rows)}"]
    if deficits:
        parts.append(
            f"без единого обращения: {len(deficits)} (например №{deficits[0]['vacancy_id']} «{deficits[0]['title'][:60]}»)"
        )
    if with_interest:
        parts.append(f"с обращениями: {len(with_interest)}")
    if top and top["signals"]:
        parts.append(f"больше всего откликов у №{top['vacancy_id']} — сигналов {top['signals']}, кандидатов {top['candidates']}")
    return "; ".join(parts) + "."


def demand_brief(demand: dict | None, limit: int = 4) -> str:
    """Compact form for the planner prompt."""
    if not demand:
        return ""
    deficits = demand.get("deficits") or []
    if not deficits:
        return ""
    lines = [
        f"- №{item['vacancy_id']} «{item['title'][:70]}»: обращений 0, публикаций {item['published_posts']}"
        for item in deficits[:limit]
    ]
    return (
        "Вакансии без единого обращения кандидатов — приоритет внимания:\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Attribution: post -> interaction -> signal -> candidate
#
# The chain already exists in the schema. These helpers read it; they never
# create a second candidate store and never infer a contact that the operator
# did not confirm.
# ---------------------------------------------------------------------------

def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def candidate_identity(candidate: Candidate | None) -> str:
    """A stable identity for one person, used to count people, not records.

    A Threads handle is unique on the platform. When it is missing, the
    confirmed contact is the next best identity. Only then does the record id
    stand in, because an anonymous record cannot be merged with anything.
    """
    if candidate is None:
        return ""
    handle = normalize_username(candidate.threads_username or "")
    if handle:
        return f"handle:{handle}"
    contact = (candidate.contact_value or candidate.contact or "").strip().casefold()
    if contact:
        return f"contact:{contact}"
    return f"candidate:{candidate.id}"


_STAGE_RANK = {"NEW": 0, "CONTACTED": 1, "QUALIFIED": 2, "HIRED": 3}


def _empty_attribution() -> dict:
    return {
        "signals": 0,
        "unique_people": 0,
        "candidates": 0,
        "contacted": 0,
        "qualified": 0,
        "hired": 0,
        "rejected": 0,
        "confirmed_contacts": 0,
        "candidate_keys": [],
        "people_keys": [],
    }


async def post_attribution(
    db,
    post_ids: list[int],
    *,
    published_by_post: dict[int, datetime] | None = None,
    window_hours: float | None = None,
) -> dict[int, dict]:
    """Signals and confirmed candidates per post, optionally inside a window.

    ``window_hours`` keeps only replies that arrived between publication and
    publication + N hours. Two posts of different age then contribute the same
    amount of observation time, which is the whole point of the experiment
    windows. A signal ignored by the operator is never counted as interest.
    """
    ids = sorted({int(item) for item in post_ids if item is not None})
    if not ids:
        return {}
    rows = (await db.execute(
        select(
            ThreadsInteraction.post_id.label("post_id"),
            ThreadsInteraction.username.label("interaction_username"),
            ThreadsInteraction.occurred_at.label("occurred_at"),
            ThreadsInteraction.ignored.label("interaction_ignored"),
            CandidateSignal.id.label("signal_id"),
            CandidateSignal.status.label("signal_status"),
            CandidateSignal.candidate_id.label("signal_candidate_id"),
            Candidate.threads_username.label("candidate_username"),
            Candidate.contact_value.label("candidate_contact"),
            Candidate.confirmed_at.label("candidate_confirmed_at"),
            Candidate.status.label("candidate_status"),
        )
        .join(CandidateSignal, CandidateSignal.interaction_id == ThreadsInteraction.id)
        .outerjoin(Candidate, Candidate.id == CandidateSignal.candidate_id)
        .where(ThreadsInteraction.post_id.in_(ids))
    )).mappings().all()

    people: dict[int, set[str]] = {post_id: set() for post_id in ids}
    candidate_ids: dict[int, set[int]] = {post_id: set() for post_id in ids}
    confirmed: dict[int, set[int]] = {post_id: set() for post_id in ids}
    contacted: dict[int, set[int]] = {post_id: set() for post_id in ids}
    qualified: dict[int, set[int]] = {post_id: set() for post_id in ids}
    hired: dict[int, set[int]] = {post_id: set() for post_id in ids}
    rejected: dict[int, set[int]] = {post_id: set() for post_id in ids}
    identity: dict[int, set[str]] = {post_id: set() for post_id in ids}
    signals: dict[int, int] = {post_id: 0 for post_id in ids}

    for row in rows:
        post_id = row["post_id"]
        if post_id not in signals:
            continue
        if row["signal_status"] == CandidateSignalStatus.IGNORED or row["interaction_ignored"]:
            continue
        occurred = _utc(row["occurred_at"])
        if window_hours is not None:
            start = _utc((published_by_post or {}).get(post_id))
            if start is None or occurred is None:
                continue
            if occurred < start or occurred > start + timedelta(hours=window_hours):
                continue
        signals[post_id] += 1
        handle = normalize_username(row["interaction_username"])
        if handle:
            people[post_id].add(handle)
        candidate_id = row["signal_candidate_id"]
        if candidate_id is None:
            continue
        candidate_ids[post_id].add(candidate_id)
        identity[post_id].add(
            f"handle:{normalize_username(row['candidate_username'])}"
            if normalize_username(row["candidate_username"])
            else (
                f"contact:{str(row['candidate_contact']).strip().casefold()}"
                if row["candidate_contact"] else f"candidate:{candidate_id}"
            )
        )
        if row["candidate_confirmed_at"] is not None:
            confirmed[post_id].add(candidate_id)
        status = str(row["candidate_status"] or "NEW")
        rank = _STAGE_RANK.get(status, 0)
        if rank >= 1:
            contacted[post_id].add(candidate_id)
        if rank >= 2:
            qualified[post_id].add(candidate_id)
        if rank >= 3:
            hired[post_id].add(candidate_id)
        if status == "REJECTED":
            rejected[post_id].add(candidate_id)

    result: dict[int, dict] = {}
    for post_id in ids:
        result[post_id] = {
            "signals": signals[post_id],
            "unique_people": len(people[post_id]),
            "candidates": len(candidate_ids[post_id]),
            "contacted": len(contacted[post_id]),
            "qualified": len(qualified[post_id]),
            "hired": len(hired[post_id]),
            "rejected": len(rejected[post_id]),
            "confirmed_contacts": len(confirmed[post_id]),
            "candidate_keys": sorted(identity[post_id]),
            "people_keys": sorted(people[post_id]),
        }
    return result


async def attribution_quality(db) -> dict:
    """How trustworthy the post -> candidate chain currently is."""
    # Aggregated in SQL (audit I-8/D7): the chain health used to load every
    # signal, interaction and candidate row into Python. The counting rules
    # are unchanged: an ignored signal is not interest, a signal without a
    # post cannot be attributed, and it also cannot be tied to a vacancy.
    chain = (await db.execute(
        select(
            func.count(CandidateSignal.id).label("total"),
            func.sum(case((CandidateSignal.status == CandidateSignalStatus.IGNORED, 1), else_=0)).label("ignored"),
            func.sum(case((CandidateSignal.status != CandidateSignalStatus.IGNORED, 1), else_=0)).label("active"),
            func.sum(case((
                (CandidateSignal.status != CandidateSignalStatus.IGNORED)
                & (CandidateSignal.vacancy_id.is_(None))
                # The outer join leaves post_id NULL both for a missing
                # interaction and for an interaction without a post, so this
                # condition reproduces the "reached a post" filter exactly.
                & ThreadsInteraction.post_id.is_not(None),
                1), else_=0)).label("without_vacancy"),
            func.count(func.distinct(case(
                (CandidateSignal.status != CandidateSignalStatus.IGNORED, ThreadsInteraction.post_id),
                else_=None,
            ))).label("posts_with_signals"),
            func.sum(case((
                (CandidateSignal.status != CandidateSignalStatus.IGNORED)
                & ThreadsInteraction.post_id.is_(None),
                1), else_=0)).label("active_without_post"),
        )
        .outerjoin(ThreadsInteraction, ThreadsInteraction.id == CandidateSignal.interaction_id)
    )).mappings().one()
    signals_total = int(chain["total"] or 0)
    ignored = int(chain["ignored"] or 0)
    without_post = int(chain["active_without_post"] or 0)
    without_vacancy = int(chain["without_vacancy"] or 0)
    posts_with_signals = int(chain["posts_with_signals"] or 0)

    candidates_without_contact = int((await db.execute(
        select(func.count(Candidate.id)).where(Candidate.confirmed_at.is_(None))
    )).scalar_one())
    candidates_without_consent = int((await db.execute(
        select(func.count(Candidate.id)).where(Candidate.consent_given.is_(False))
    )).scalar_one())

    # Grouping by the normalized handle has to happen in Python: the username
    # normalization is application logic, no SQL equivalent. The CRM is
    # person-scale, so this stays bounded.
    candidates = list((await db.execute(select(Candidate))).scalars())
    by_handle: dict[str, int] = {}
    for candidate in candidates:
        handle = normalize_username(candidate.threads_username or "")
        if handle:
            by_handle[handle] = by_handle.get(handle, 0) + 1
    duplicate_handles = sum(1 for count in by_handle.values() if count > 1)
    duplicate_records = sum(count - 1 for count in by_handle.values() if count > 1)

    return {
        "signals_total": signals_total,
        "signals_ignored": ignored,
        "signals_without_post": without_post,
        "signals_without_vacancy": without_vacancy,
        "candidates_total": len(candidates),
        "candidates_without_contact": candidates_without_contact,
        "candidates_without_consent": candidates_without_consent,
        "duplicate_handles": duplicate_handles,
        "duplicate_candidate_records": duplicate_records,
        "posts_with_signals": posts_with_signals,
        "identity": "threads_username → confirmed contact → record id",
        "limitations": [
            "Сигнал — это интерес, а не кандидат. Кандидатом человек становится только после подтверждённого контакта.",
            "Сигналы без привязки к посту нельзя отнести к конкретной публикации.",
            "Этап «приглашение на собеседование» в CRM не ведётся, поэтому он показывается как N/A, а не как ноль.",
        ],
    }


async def vacancy_funnel(db, *, vacancy_id: int | None = None) -> list[dict]:
    """The whole funnel for a vacancy, with distinct people at every step."""
    query = select(Vacancy).order_by(Vacancy.created_at.desc())
    if vacancy_id is not None:
        query = query.where(Vacancy.id == vacancy_id)
    vacancies = list((await db.execute(query)).scalars())
    if not vacancies:
        return []

    posts = {row.id: row for row in (await db.execute(select(ThreadsPost))).scalars()}
    interactions = {
        row.id: row for row in (await db.execute(select(ThreadsInteraction))).scalars()
    }
    signals = list((await db.execute(select(CandidateSignal))).scalars())
    candidates = {
        row.id: row for row in (await db.execute(select(Candidate))).scalars()
    }

    state: dict[int, dict] = {}
    for vacancy in vacancies:
        state[vacancy.id] = {
            "published_posts": sum(
                1 for post in posts.values()
                if post.vacancy_id == vacancy.id and post.status == PostStatus.PUBLISHED
            ),
            "signals": 0,
            "people": set(),
            "candidates": set(),
            "contacted": set(),
            "qualified": set(),
            "hired": set(),
            "rejected": set(),
        }
    for signal in signals:
        if signal.status == CandidateSignalStatus.IGNORED:
            continue
        interaction = interactions.get(signal.interaction_id)
        if interaction is None or interaction.ignored:
            continue
        post = posts.get(interaction.post_id) if interaction.post_id else None
        target = signal.vacancy_id or (post.vacancy_id if post is not None else None)
        if target is None or target not in state:
            continue
        item = state[target]
        item["signals"] += 1
        handle = normalize_username(interaction.username)
        if handle:
            item["people"].add(handle)
        candidate_id = signal.candidate_id
        if candidate_id is None or candidate_id not in candidates:
            continue
        item["candidates"].add(candidate_id)
        status = str(candidates[candidate_id].status or "NEW")
        rank = _STAGE_RANK.get(status, 0)
        if rank >= 1:
            item["contacted"].add(candidate_id)
        if rank >= 2:
            item["qualified"].add(candidate_id)
        if rank >= 3:
            item["hired"].add(candidate_id)
        if status == "REJECTED":
            item["rejected"].add(candidate_id)

    rows = []
    for vacancy in vacancies:
        item = state[vacancy.id]
        rows.append({
            "vacancy_id": vacancy.id,
            "title": vacancy.title,
            "published_posts": item["published_posts"],
            "signals": item["signals"],
            "unique_people": len(item["people"]),
            "candidates": len(item["candidates"]),
            "contacted": len(item["contacted"]),
            "qualified": len(item["qualified"]),
            "hired": len(item["hired"]),
            "rejected": len(item["rejected"]),
            # The CRM has no interview stage. A zero here would be a lie.
            "invited": None,
        })
    return rows


async def attribution_report(db, *, post_id: int | None = None, vacancy_id: int | None = None) -> dict:
    """The funnel per post and per vacancy, plus the health of the chain."""
    query = select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.PUBLISHED,
        ThreadsPost.remote_id.is_not(None),
        ~ThreadsPost.remote_id.like("mock-%"),
    )
    if post_id is not None:
        query = select(ThreadsPost).where(ThreadsPost.id == post_id)
    if vacancy_id is not None:
        query = query.where(ThreadsPost.vacancy_id == vacancy_id)
    posts = list((await db.execute(
        query.order_by(ThreadsPost.published_at.desc(), ThreadsPost.id.desc()).limit(50)
    )).scalars())
    attribution = await post_attribution(db, [post.id for post in posts])
    post_rows = []
    for post in posts:
        item = attribution.get(post.id, _empty_attribution())
        post_rows.append({
            "post_id": post.id,
            "vacancy_id": post.vacancy_id,
            "content": (post.content or "")[:160],
            "published_at": post.published_at.isoformat() if post.published_at else None,
            "signals": item["signals"],
            "unique_people": item["unique_people"],
            "candidates": item["candidates"],
            "contacted": item["contacted"],
            "qualified": item["qualified"],
            "hired": item["hired"],
            "rejected": item["rejected"],
        })
    return {
        "quality": await attribution_quality(db),
        "posts": post_rows,
        "vacancies": await vacancy_funnel(db, vacancy_id=vacancy_id),
        "limitations": [
            "Порядок оценки: кандидаты → сигналы → охват. Просмотры не являются целью.",
            "Один человек считается один раз на пост, даже если написал несколько комментариев.",
            "Пустые этапы воронки означают «неизвестно» только для приглашений; остальные этапы ведутся в CRM.",
        ],
    }

