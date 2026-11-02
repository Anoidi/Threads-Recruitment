from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import desc, select

from app.config import settings
from app.enums import AgentRole, PostStatus, TopicStatus
from app.models import AgentRun, PostMetric, ThreadsPost, Topic, Vacancy
from app.services import activity
from app.threads_client import ThreadsAPIError


async def run_agent(db, run_id: int, role: AgentRole) -> None:
    from . import main as _m

    run = await db.get(AgentRun, run_id)
    if not run:
        return
    started = _m.monotonic()
    run.status = "RUNNING"
    run.started_at = datetime.now(timezone.utc)
    await db.commit()
    try:
        if role == AgentRole.COMMUNITY:
            await _m.sync_community(db)
            summary = "Входящие Threads синхронизированы и классифицированы."
        elif role == AgentRole.SCOUT:
            summary = await _m.scout_sources(db)
        elif role == AgentRole.STRATEGIST:
            vacancies = list(
                (
                    await db.execute(
                        select(Vacancy).where(Vacancy.active.is_(True)).order_by(desc(Vacancy.created_at)).limit(5)
                    )
                ).scalars()
            )
            summary = await _m.strategize(db, vacancies)
        elif role == AgentRole.ANALYST:
            summary = await _m._analyst_summary(db)
        elif role == AgentRole.REPLIER:
            summary = await _m.draft_replies(db, _m.REPLY_DRAFTS_PER_RUN)
        elif role == AgentRole.EXPERIMENTER:
            summary = await _m.run_experimenter(db)
        else:
            summary = await _m.content_plan(
                db,
                {
                    "count": 1,
                    "language": settings.default_language,
                    "editor": "writer-agent",
                },
            )
    except Exception as exc:
        await db.rollback()
        failed_run = await db.get(AgentRun, run_id)
        if failed_run is not None:
            is_meta_block = isinstance(exc, ThreadsAPIError) and exc.category == "app_blocked"
            failed_run.status = "BLOCKED" if is_meta_block else "FAILED"
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.duration_ms = int((_m.monotonic() - started) * 1000)
            trace = f" Trace ID: {exc.fbtrace_id}." if is_meta_block and exc.fbtrace_id else ""
            failed_run.error = f"{type(exc).__name__}: {str(exc)[:1000]}{trace}"
            failed_run.result_summary = (
                "Приостановлен: Meta заблокировала Threads API. После разблокировки запустите COMMUNITY снова."
                if is_meta_block
                else None
            )
            await activity(
                db,
                "agent_blocked" if is_meta_block else "agent_failed",
                "agent_run",
                failed_run.id,
                {"role": role.value, "error_type": type(exc).__name__, "trace_id": getattr(exc, "fbtrace_id", None)},
            )
            await db.commit()
        if isinstance(exc, ThreadsAPIError) and exc.category == "app_blocked":
            return
        raise
    run.status = "COMPLETED"
    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = int((_m.monotonic() - started) * 1000)
    run.result_summary = summary
    run.error = None
    await activity(db, "agent_executed", "agent_run", run.id, {"role": role.value})
    await db.commit()


async def run_experimenter(db) -> str:
    """Keep one format experiment alive and report what it shows."""
    from app.experiments import (
        active_experiment,
        current_report,
        experiment_needs_round,
        start_experiment,
    )
    from . import main as _m

    experiment = await active_experiment(db)
    if experiment is None:
        try:
            experiment = await start_experiment(db, created_by="experimenter-agent")
        except ValueError as exc:
            await db.commit()
            return f"Эксперимент не запущен: {exc}"
        await activity(
            db,
            "experiment_started",
            "experiment",
            experiment.id,
            {
                "subject": experiment.subject,
                "fact": experiment.fact,
                "variants": [item.get("key") for item in experiment.variants or []],
                "created_by": "experimenter-agent",
            },
        )
        await db.commit()
    if not await experiment_needs_round(db, experiment):
        plan_summary = (
            f"Эксперимент #{experiment.id} набрал нужное число постов; новые посты в этом "
            "формате больше не создаются, дневной план возвращается к обычному контенту."
        )
    else:
        plan_summary = await _m.content_plan(
            db,
            {
                "count": len(experiment.variants or []),
                "language": settings.default_language,
                "editor": "experimenter-agent",
            },
        )
    report = await current_report(db)
    if report is None:
        return plan_summary
    variants = report.get("variants") or []
    detail = ", ".join(
        f"{row['label']}: опубликовано {row['published']}, кандидатов {row['candidates']}, "
        f"сигналов {row['signals']}"
        for row in variants
    )
    summary = f"{plan_summary} {report.get('verdict_text') or ''}"
    if detail:
        summary += f" По группам: {detail}."
    if report.get("excluded"):
        summary += f" Исключено из сравнения постов: {len(report['excluded'])}."
    return summary


async def _analyst_summary(db) -> str:
    """Use the same read-only editorial evidence as the dashboard and planner."""
    from app.content_analysis import editorial_evidence

    report = await editorial_evidence(db)
    if not report["posts_with_metrics"]:
        return (
            "Нет публикаций с метриками. Следующий шаг: соберите замеры опубликованных постов "
            "через 24 часа; до этого выводы о лучших форматах не делаем."
        )
    profile = report.get("audience_profile") or {}
    audience_line = ""
    if profile.get("recommendation"):
        audience_line = " Аудитория: " + str(profile["recommendation"])[:240]
    experiment_line = ""
    experiment = report.get("experiment")
    if experiment:
        variants = ", ".join(
            f"{row['label']} — постов {row['published']}, кандидатов {row['candidates']}"
            for row in (experiment.get("variants") or [])
        )
        experiment_line = (
            f" Эксперимент форматов (факт «{experiment.get('fact')}»): "
            f"{str(experiment.get('verdict_text') or '')[:300]} {variants}."
        )
    return (
        f"Публикаций с метриками: {report['posts_with_metrics']}; "
        f"сопоставимых: {report['comparable_posts']}. "
        + report["recommendation"]
        + " "
        + " ".join(report["limitations"])
        + audience_line
        + experiment_line
    )


async def _legacy_analyst_summary(db) -> str:
    """Unused historical algorithm; retained temporarily for migration review."""
    rows = (
        await db.execute(
            select(
                ThreadsPost.id,
                ThreadsPost.content_type,
                PostMetric.views,
                PostMetric.likes,
                PostMetric.replies,
                PostMetric.reposts,
                PostMetric.quotes,
                PostMetric.captured_at,
            )
            .join(PostMetric, PostMetric.post_id == ThreadsPost.id)
            .where(ThreadsPost.status == PostStatus.PUBLISHED)
            .order_by(PostMetric.post_id, desc(PostMetric.captured_at))
        )
    ).all()
    latest: dict[int, tuple] = {}
    for row in rows:
        latest.setdefault(row.id, row)
    if not latest:
        return (
            "Нет публикаций с метриками для сравнения. Следующий шаг: опубликуйте "
            "минимум 3 поста разных типов и дождитесь первого сбора метрик."
        )
    segments: dict[str, dict[str, int]] = {}
    for row in latest.values():
        name = getattr(row.content_type, "value", str(row.content_type))
        bucket = segments.setdefault(name, {"posts": 0, "views": 0, "actions": 0})
        bucket["posts"] += 1
        bucket["views"] += int(row.views or 0)
        bucket["actions"] += sum(int(value or 0) for value in (row.likes, row.replies, row.reposts, row.quotes))
    best_name, best = max(
        segments.items(),
        key=lambda item: (item[1]["actions"] / max(item[1]["views"], 1), item[1]["actions"]),
    )
    rate = best["actions"] / max(best["views"], 1) * 100
    return (
        f"Проанализировано публикаций с метриками: {len(latest)}; лучший сегмент: {best_name} "
        f"({rate:.1f}% взаимодействий, {best['views']} просмотров). "
        f"Рекомендация: подготовьте 2 новых поста типа {best_name} с разными заходами и сравните их через 24 часа."
    )


async def strategize(db, vacancies: list[Vacancy]) -> str:
    """Propose one content angle for the Strategist agent."""
    from app.recruitment_intelligence import vacancy_demand
    from . import main as _m

    if not vacancies:
        return "No active vacancies to strategize about."
    demand = await vacancy_demand(db)
    deficits = demand.get("deficits") or []
    if deficits:
        target = await db.get(Vacancy, deficits[0]["vacancy_id"])
        if target is not None:
            vacancies = [target] + [item for item in vacancies if item.id != target.id]
    latest = vacancies[0]

    def deterministic() -> None:
        db.add(
            Topic(
                title=f"Explain the real conditions for {latest.title}",
                source="strategist",
                content_type="VACANCY",
                language=latest.language,
                vacancy_id=latest.id,
                priority=80,
                explanation="Active vacancy with a factual content angle.",
                status=TopicStatus.PLANNED,
            )
        )

    fallback_summary = f"Strategy suggestion created from active vacancy #{latest.id} ({latest.title})."
    if settings.ai_mock_mode:
        deterministic()
        return fallback_summary
    facts = "\n".join(
        f"- id={v.id}: {v.title} | {v.location} | {v.salary} {v.currency} | {v.schedule or 'n/a'}"
        for v in vacancies
    )
    task = (
        "You are the strategist of a recruitment team posting on Threads. "
        "Propose exactly ONE content angle for the next post using only the vacancy facts below. "
        "Do not invent salaries, conditions or locations. "
        'Answer with a single JSON object: {"title": short angle title (max 120 chars), '
        '"explanation": 1-2 sentences on why it works, "language": one of uk|pl|ru|en, '
        '"vacancy_id": the vacancy id this angle is based on}.\n\nVacancies:\n' + facts
    )
    try:
        if settings.hermes_for_strategist:
            proposal = _m.hermes_client.parse_json(await _m.hermes_client.ask(task, max_tokens=500))
        else:
            proposal = await _m.AIService().strategy_proposal(task)
    except Exception as exc:
        deterministic()
        return f"{fallback_summary} Model fallback: {type(exc).__name__}: {str(exc)[:160]}"
    title = str(proposal.get("title") or "").strip()[:255]
    language = str(proposal.get("language") or latest.language).strip()[:2]
    if not title or language not in {"uk", "pl", "ru", "en"}:
        deterministic()
        return f"{fallback_summary} The model returned an unusable proposal; stored the deterministic angle."
    vacancy_id = proposal.get("vacancy_id")
    try:
        vacancy_id = int(vacancy_id)
    except (TypeError, ValueError):
        vacancy_id = -1
    valid_ids = {v.id for v in vacancies}
    if vacancy_id not in valid_ids:
        vacancy_id = latest.id

    def _similar(left: str, right: str) -> bool:
        left_words = {word for word in re.findall(r"\w+", left.casefold()) if len(word) > 2}
        right_words = {word for word in re.findall(r"\w+", right.casefold()) if len(word) > 2}
        if not left_words or not right_words:
            return False
        return len(left_words & right_words) / min(len(left_words), len(right_words)) >= 0.6

    existing = (
        await db.execute(select(Topic).where(Topic.status.notin_([TopicStatus.REJECTED, TopicStatus.ARCHIVED])))
    ).scalars()
    for topic in existing:
        if _similar(topic.title or "", title):
            return f"Strategy angle already stored: {topic.title}"
    db.add(
        Topic(
            title=title,
            source="strategist",
            content_type="VACANCY",
            language=language,
            vacancy_id=vacancy_id,
            priority=80,
            explanation=str(proposal.get("explanation") or "")[:2000] or None,
            status=TopicStatus.PLANNED,
        )
    )
    return f"Strategy angle proposed for vacancy #{vacancy_id}: {title}"
