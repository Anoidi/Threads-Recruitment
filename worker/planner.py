from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import desc, func, select

from app.config import settings
from app.content_quality import render_vacancy_post
from app.content_subjects import primary_subject, subjects_of
from app.enums import PostStatus, TopicStatus
from app.models import (
    AppSetting,
    PostRevision,
    ThreadExperimentAssignment,
    ThreadsInteraction,
    ThreadsPost,
    Topic,
    Vacancy,
)
from app.post_validation import validate_post_for_publication
from app.services import activity

# How long a published angle blocks the same angle again.
CONTENT_DEDUP_WINDOW_DAYS = 21


def _match_vacancy(vacancies, content: str) -> int | None:
    """Find the vacancy whose confirmed conditions a post actually quotes."""
    from app.content_quality import structured_vacancy_facts

    text = " ".join((content or "").casefold().split())
    if not text:
        return None
    best_id, best_score = None, 0
    for vacancy in vacancies:
        facts = structured_vacancy_facts(vacancy)
        score = 0
        for value in (
            facts.salary,
            facts.student_salary,
            facts.schedule,
            facts.housing,
            facts.location,
            facts.contract_type,
        ):
            if not value:
                continue
            probe = " ".join(str(value).casefold().split())
            if len(probe) >= 4 and probe in text:
                score += 1
        if score > best_score:
            best_id, best_score = vacancy.id, score
    return best_id if best_score >= 2 else None


def _substitute_angle(
    vacancies,
    language: str,
    phone_contact: str,
    seen_content: set[str],
    normalize_content,
    used_subjects: set[str] | None = None,
    preferred_subject: str | None = None,
    variant: int = 0,
) -> tuple[str, str, str, int, str] | None:
    """Replace a repeated idea with a deterministic, source-grounded post."""
    from app.editorial_angles import EDITORIAL_ANGLES, angle_content_type, angle_hook, angle_post, angle_subject

    taken = used_subjects or set()
    order = list(EDITORIAL_ANGLES)
    shift = int(variant) % len(order)
    order = order[shift:] + order[:shift]
    if preferred_subject:
        preferred = [angle for angle in order if angle_subject(angle) == preferred_subject]
        order = preferred + [angle for angle in order if angle not in preferred]
    for vacancy in vacancies:
        for angle in order:
            subject = angle_subject(angle)
            if subject in taken:
                continue
            text = angle_post(vacancy, angle, language, phone_contact, variant=variant)
            if not text:
                continue
            if normalize_content(text) in seen_content:
                continue
            content_type = angle_content_type(angle)
            try:
                validate_post_for_publication(
                    content=text, language=language, vacancy=vacancy, content_type=content_type
                )
            except HTTPException:
                continue
            return text, content_type, angle_hook(text), vacancy.id, subject
    return None


async def run_experiment_plan(db, experiment, *, language: str, editor: str) -> str:
    """One post per variant of a running format experiment."""
    from app.experiments import (
        FORMAT_INSTRUCTIONS,
        base_fact_text,
        record_assignment,
        render_format,
        slot_rotation,
    )
    from . import main as _m

    vacancy = await db.get(Vacancy, experiment.vacancy_id) if experiment.vacancy_id else None
    if vacancy is None:
        summary = (
            f"Эксперимент #{experiment.id} остановлен: вакансия с подтверждённым фактом "
            "больше недоступна."
        )
        await activity(db, "experiment_blocked", "experiment", experiment.id, {"reason": "vacancy_missing"})
        await db.commit()
        return summary
    phone_contact = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))
    ).scalar_one_or_none()
    variants = experiment.variants or []
    verified_before = (
        await db.execute(
            select(func.count(ThreadExperimentAssignment.id)).where(
                ThreadExperimentAssignment.experiment_id == experiment.id,
                ThreadExperimentAssignment.verified.is_(True),
            )
        )
    ).scalar_one()
    round_index = int(verified_before or 0) // max(1, len(variants))
    parts = base_fact_text(vacancy, experiment.subject, language, str(phone_contact or ""), variant=round_index)
    if parts is None:
        summary = (
            f"Эксперимент #{experiment.id} остановлен: у вакансии больше нет подтверждённого "
            f"факта по теме «{experiment.subject}»."
        )
        await activity(db, "experiment_blocked", "experiment", experiment.id, {"reason": "fact_missing"})
        await db.commit()
        return summary
    sentences, cta = parts
    base_text = " ".join(sentences)
    voice = (await db.execute(select(AppSetting.value).where(AppSetting.key == "brand_voice"))).scalar_one_or_none()
    created = 0
    verified = 0
    excluded = 0
    fallback_error: str | None = None
    slots = await _m._next_publish_slots(db, datetime.now(timezone.utc), len(variants))
    rotation = slot_rotation(experiment.id, round_index, len(variants))
    slot_by_variant = {index: slots[rotation.get(index, index)] for index in range(len(variants))}
    for index, variant in enumerate(variants):
        key = str(variant.get("key") or variant.get("format") or "")
        fmt = str(variant.get("format") or key)
        content: str | None = None
        hook: str | None = None
        rationale: str | None = None
        content_type = "FAQ"
        try:
            ideas = await _m.AIService().plan_content(
                vacancies=[vacancy],
                audience_questions=None,
                trend_topics=[],
                editorial_analysis=None,
                language=language,
                brand_voice=str(voice) if voice else "Professional, helpful, factual and human",
                count=1,
                format_instruction=FORMAT_INSTRUCTIONS.get(fmt),
                fact_text=experiment.fact,
                base_text=base_text,
                subject_label=experiment.subject,
            )
        except HTTPException as exc:
            fallback_error = f"{exc.status_code}: {exc.detail}"
            ideas = []
        if ideas:
            idea = ideas[0]
            content = str(idea.get("content") or "").strip()
            hook = str(idea.get("hook") or content.split("\n", 1)[0]).strip()[:500] or None
            rationale = str(idea.get("rationale") or "")[:1000] or None
            content_type = str(idea.get("content_type") or "FAQ").upper()
        if not content:
            content = render_format(fmt, sentences, cta, language)
            if content is None:
                await activity(
                    db,
                    "experiment_variant_skipped",
                    "experiment",
                    experiment.id,
                    {"variant": key, "reason": "no_content"},
                )
                continue
            hook = content.split("\n", 1)[0].strip()[:500] or None
            content_type = {"FACT": "FAQ", "QUESTION": "QUESTION", "TIPS": "EDUCATIONAL"}.get(fmt, "FAQ")
            rationale = "Детерминированная версия того же подтверждённого факта в заданном формате."
        post = ThreadsPost(
            vacancy_id=vacancy.id,
            hook=hook,
            content=content,
            content_type=content_type,
            language=language,
            reasoning_summary=rationale,
            content_subject=experiment.subject,
            cause_type="EXPERIMENT",
            cause_ref=f"{experiment.id}:{key}",
            hypothesis=(
                f"Формат «{fmt}» на одном и том же факте «{experiment.fact}»: "
                "кандидаты важнее просмотров."
            ),
            status=PostStatus.AI_GENERATED,
        )
        db.add(post)
        await db.flush()
        db.add(PostRevision(post_id=post.id, version=1, content=post.content, editor=editor))
        assignment = await record_assignment(db, experiment, post, key, fmt)
        created += 1
        if assignment.verified:
            verified += 1
        else:
            excluded += 1
        await activity(
            db,
            "experiment_post_created",
            "post",
            post.id,
            {
                "experiment_id": experiment.id,
                "variant": key,
                "format": fmt,
                "verified": assignment.verified,
                "mismatch_reason": assignment.mismatch_reason,
            },
        )
        await _m._auto_schedule_post(db, post, slot=slot_by_variant.get(index))
    await activity(
        db,
        "experiment_plan_generated",
        "experiment",
        experiment.id,
        {
            "created": created,
            "verified": verified,
            "excluded": excluded,
            "fact": experiment.fact,
            "subject": experiment.subject,
            "slots": [slot.isoformat() for slot in slots],
        },
    )
    await db.commit()
    summary = (
        f"Эксперимент #{experiment.id}: факт «{experiment.fact}» ({experiment.subject}), "
        f"форматов {len(experiment.variants or [])}, создано постов {created}, "
        f"прошли проверку формата {verified}, исключены из сравнения {excluded}."
    )
    if fallback_error:
        summary += f" Резервный детерминированный формат использован после ошибки ИИ ({fallback_error[:160]})."
    return summary + "."


async def content_plan(db, payload: dict) -> str:
    """Create audience-content drafts (not only vacancies) with the local AI."""
    from app.content_analysis import editorial_evidence
    from app.experiments import active_experiment, experiment_needs_round
    from . import main as _m

    evidence = await editorial_evidence(db)
    language = str(payload.get("language") or settings.default_language).strip()[:2] or settings.default_language
    try:
        count = int(payload.get("count", 3))
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(count, 6))
    editor = str(payload.get("editor") or "planner-agent")[:80]

    experiment = await active_experiment(db)
    if not payload.get("stock_refill") and experiment is not None and await experiment_needs_round(db, experiment):
        return await _m.run_experiment_plan(db, experiment, language=language, editor=editor)
    vacancy_query = select(Vacancy).where(Vacancy.active.is_(True))
    if payload.get("stock_refill"):
        # Refill cannot depend on facts that still require a human confirmation.
        vacancy_query = vacancy_query.where(
            Vacancy.source_confirmed_at >= datetime.now(timezone.utc) - timedelta(days=15),
            Vacancy.source_drifted_at.is_(None),
        )
    vacancies = list((await db.execute(vacancy_query.order_by(desc(Vacancy.created_at)).limit(3))).scalars())
    audience_rows = list(
        (
            await db.execute(select(ThreadsInteraction).order_by(desc(ThreadsInteraction.occurred_at)).limit(80))
        ).scalars()
    )
    from app.audience_editorial import select_audience_questions
    audience = select_audience_questions(audience_rows)
    trend_rows = list(
        (
            await db.execute(
                select(Topic)
                .where(
                    Topic.status.in_([TopicStatus.DISCOVERED, TopicStatus.PLANNED]),
                    Topic.source.in_(("threads-keyword-search", "google-news-rss", "strategist")),
                    Topic.created_at >= datetime.now(timezone.utc) - timedelta(days=14),
                )
                .order_by(desc(Topic.priority), desc(Topic.created_at))
                .limit(6)
            )
        ).scalars()
    )
    trend_topics = [re.sub(r"[\[\]]", "", topic.title)[:160] for topic in trend_rows]
    voice = (await db.execute(select(AppSetting.value).where(AppSetting.key == "brand_voice"))).scalar_one_or_none()
    phone_contact = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))
    ).scalar_one_or_none()
    # Only published posts define what "already covered" means: drafts,
    # deterministic substitutes and blocked duplicates must not consume the
    # angle space, and coverage older than the dedup window must let go.
    recent_subjects = subjects_of(
        (
            await db.execute(
                select(ThreadsPost.content)
                .where(
                    ThreadsPost.status == PostStatus.PUBLISHED,
                    ThreadsPost.created_at
                    >= datetime.now(timezone.utc) - timedelta(days=CONTENT_DEDUP_WINDOW_DAYS),
                )
                .order_by(desc(ThreadsPost.created_at))
                .limit(40)
            )
        ).scalars()
    )
    fallback_error: str | None = None
    from app.editorial_memory import approved_rules

    ai = _m.AIService()
    ai.account_contact = str(phone_contact or "")
    # Close the operator-feedback loop: confirmed editorial preferences must
    # reach the daily planner too, not only the manual generation path.
    ai.editorial_rules = await approved_rules(db, language, None)
    try:
        ideas = await ai.plan_content(
            vacancies=vacancies,
            audience_questions=[(index, text) for index, (_, text) in enumerate(audience)],
            trend_topics=trend_topics,
            editorial_analysis=evidence,
            language=language,
            brand_voice=str(voice) if voice else "Professional, helpful, factual and human",
            count=count,
            covered_subjects=recent_subjects,
        )
    except HTTPException as exc:
        fallback_error = f"{exc.status_code}: {exc.detail}"
        candidate_count = max(count, 4) if payload.get("editor") == "writer-agent" else count
        ideas = _m.AIService._mock_plan(vacancies, language, candidate_count, trend_topics)
    created: list[int] = []
    needs_split: list[int] = []
    used_topic_ids: set[int] = set()
    skipped = 0
    substituted_total = 0
    valid_ids = {vacancy.id for vacancy in vacancies}
    latest_id = vacancies[0].id if vacancies else None
    window_start = datetime.now(timezone.utc) - timedelta(days=_m.CONTENT_DEDUP_WINDOW_DAYS)
    recent_content = list(
        (
            await db.execute(
                select(ThreadsPost.content)
                .where(ThreadsPost.created_at >= window_start)
                .order_by(desc(ThreadsPost.created_at), desc(ThreadsPost.id))
                .limit(150)
            )
        ).scalars()
    )
    normalize_content = lambda value: " ".join(value.split()).casefold()
    seen_content = {normalize_content(value) for value in recent_content}
    normalize_template = lambda value: normalize_content(re.sub(r"«[^»]*»", "«TOPIC»", value))
    seen_templates = {normalize_template(value) for value in recent_content}
    used_subjects: set[str] = set()
    plan_variant = datetime.now(timezone.utc).timetuple().tm_yday
    audience_profile = evidence.get("audience_profile") if isinstance(evidence, dict) else None
    gap_theme = None
    for gap in (audience_profile or {}).get("gaps") or []:
        theme = str(gap.get("theme") or "")
        if theme and theme not in used_subjects:
            gap_theme = theme
            break
    for idea in ideas:
        content = str(idea.get("content") or "").strip()
        if not content:
            continue
        hook = str(idea.get("hook") or content.split("\n", 1)[0]).strip()[:500] or None
        content_type = str(idea.get("content_type") or "ENGAGEMENT").upper()
        vacancy_id = idea.get("vacancy_id")
        if content_type == "VACANCY":
            vacancy_id = vacancy_id if isinstance(vacancy_id, int) and vacancy_id in valid_ids else latest_id
            if vacancy_id is None:
                continue
            selected_vacancy = next((item for item in vacancies if item.id == vacancy_id), None)
            if selected_vacancy is not None:
                try:
                    content = render_vacancy_post(selected_vacancy, language, str(phone_contact or ""))
                except ValueError:
                    needs_split.append(selected_vacancy.id)
                    await activity(
                        db,
                        "vacancy_post_requires_split",
                        "vacancy",
                        selected_vacancy.id,
                        {"reason": "All rates and conditions exceed the platform limit; no truncated post created"},
                    )
                    continue
                hook = selected_vacancy.title[:500]
                subject = "VACANCY"
        else:
            vacancy_id = None
        if vacancy_id is None and content_type != "VACANCY":
            matched = _m._match_vacancy(vacancies, content)
            if matched is not None:
                vacancy_id = matched
        fingerprint = normalize_content(content)
        template_fingerprint = (
            normalize_template(content) if content_type in {"TREND_RESPONSE", "ENGAGEMENT"} else fingerprint
        )
        duplicate = fingerprint in seen_content or (
            fallback_error
            and content_type in {"TREND_RESPONSE", "ENGAGEMENT"}
            and template_fingerprint in seen_templates
        )
        subject = primary_subject(content)
        rationale = str(idea.get("rationale") or "")[:1000] or None
        substituted = False
        if duplicate or subject in used_subjects:
            skipped += 1
            substitute = _m._substitute_angle(
                vacancies,
                language,
                str(phone_contact or ""),
                seen_content,
                normalize_content,
                used_subjects,
                gap_theme,
                variant=plan_variant,
            )
            if substitute is None:
                continue
            content, content_type, hook, vacancy_id, subject = substitute
            if subject in used_subjects:
                continue
            rationale = "Детерминированный ракурс по подтверждённым условиям вакансии; факты взяты из источника."
            substituted = True
            substituted_total += 1
        used_subjects.add(subject)
        seen_content.add(normalize_content(content))
        seen_templates.add(normalize_template(content))
        cause_type, cause_ref = "THEME", subject
        question_index = idea.get("source_question_index")
        if substituted:
            cause_type, cause_ref = "THEME", subject
        elif type(question_index) is int and 0 <= question_index < len(audience):
            cause_type, cause_ref = "AUDIENCE_QUESTION", str(audience[question_index][0])
        elif type(idea.get("source_topic_index")) is int and 0 <= idea["source_topic_index"] < len(trend_rows):
            cause_type, cause_ref = "TREND", str(trend_rows[idea["source_topic_index"]].id)
        hypothesis = None
        if cause_type == "AUDIENCE_QUESTION":
            hypothesis = f"Ответ на вопрос аудитории: {audience[question_index][1][:160]}"
        elif cause_type == "TREND":
            hypothesis = f"Реакция на тему: {trend_rows[idea['source_topic_index']].title[:160]}"
        post = ThreadsPost(
            vacancy_id=vacancy_id,
            hook=hook,
            content=content,
            content_type=content_type,
            language=str(idea.get("language") or language)[:2],
            reasoning_summary=rationale,
            content_subject=subject,
            cause_type=cause_type,
            cause_ref=cause_ref,
            hypothesis=hypothesis,
            status=PostStatus.AI_GENERATED,
        )
        db.add(post)
        await db.flush()
        db.add(PostRevision(post_id=post.id, version=1, content=post.content, editor=editor))
        if fallback_error and not substituted:
            post.reasoning_summary = (
                f"Резервный шаблон после ошибки ИИ; требуется проверка оператором. {post.reasoning_summary or ''}"[
                    :2000
                ]
            )
        else:
            await _m._auto_schedule_post(db, post)
        created.append(post.id)
        source_index = idea.get("source_topic_index")
        if type(source_index) is int and 0 <= source_index < len(trend_rows) and content_type != "VACANCY":
            used_topic_ids.add(trend_rows[source_index].id)
            await activity(
                db,
                "post_research_source",
                "post",
                post.id,
                {
                    "topic_id": trend_rows[source_index].id,
                    "source": trend_rows[source_index].source,
                    "attribution": "planner_declared_not_fact_verification",
                },
            )
        if len(created) >= count:
            break
    await activity(
        db,
        "content_plan_generated",
        "post",
        created[0] if created else None,
        {
            "created": len(created),
            "skipped": skipped,
            "substituted": substituted_total,
            "language": language,
            "vacancies_requiring_split": needs_split,
        },
    )
    if used_topic_ids:
        for topic in trend_rows:
            if topic.id not in used_topic_ids:
                continue
            topic.status = TopicStatus.USED
    await db.commit()
    summary = f"Planned {len(created)} audience draft(s) in {language}"
    if skipped:
        summary += f", {skipped} duplicate(s) skipped"
    if substituted_total:
        summary += f", {substituted_total} replaced with a source-grounded angle"
    if needs_split:
        summary += ". Требуется разделить условия вакансий: " + ", ".join(map(str, needs_split))
    if fallback_error:
        summary += f". Резервный локальный шаблон использован после ошибки ИИ ({fallback_error[:180]})"
    return summary + "."
