"""What actually worked, measured per cause, per theme and per format.

The system could measure the reach of a post, but the result never reached
the planner. So the planner chose topics by what people asked and published
twenty posts on a theme that performs worse than the one nobody answered.

This module closes that loop. It reports, for every published post, the
cause it was published for, and then groups the measured result by that
cause, by theme and by format. The planner receives the compact form.

The samples are small, and the report says so: a median over four posts is a
hint, not a finding. The numbers are therefore always reported together with
the number of posts and the age of the posts, and the prompt tells the
model to treat them as a direction, not as a proven rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select

from .content_subjects import theme_of
from .enums import CandidateSignalStatus, PostStatus
from .models import Candidate, CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost


def _median(values: list[int]) -> int | None:
    return int(median(values)) if values else None


def _response_rate(post, metric) -> float | None:
    """Actions per 100 views at age 24h–7d, not a unique-person conversion rate."""
    if metric is None or not post.published_at or not metric.captured_at:
        return None
    published, captured = post.published_at, metric.captured_at
    published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published
    captured = captured.replace(tzinfo=timezone.utc) if captured.tzinfo is None else captured
    if not timedelta(days=1) <= captured - published <= timedelta(days=7):
        return None
    values = [getattr(metric, field, None) for field in ("views", "likes", "replies", "reposts", "quotes")]
    if any(value is None or value < 0 for value in values) or values[0] == 0:
        return None
    return sum(values[1:]) / values[0] * 100


async def _latest_metrics(db, posts: list[ThreadsPost]) -> dict[int, PostMetric]:
    if not posts:
        return {}
    ids = [post.id for post in posts]
    rows = list((await db.execute(
        select(PostMetric).where(PostMetric.post_id.in_(ids)).order_by(PostMetric.captured_at, PostMetric.id)
    )).scalars())
    latest: dict[int, PostMetric] = {}
    for row in rows:
        latest[row.post_id] = row
    return latest


async def content_evidence(db, *, days: int = 30) -> dict:
    """Measured results grouped by cause, theme and format."""
    now = datetime.now(timezone.utc)
    # The window is real: evidence older than `days` must not keep steering
    # the planner toward the same winning theme forever, or the duplicate
    # gate bans that theme and the feed degrades into filler.
    posts = list((await db.execute(
        select(ThreadsPost).where(
            ThreadsPost.status == PostStatus.PUBLISHED,
            ThreadsPost.remote_id.is_not(None),
            ~ThreadsPost.remote_id.like("mock-%"),
            ThreadsPost.published_at >= now - timedelta(days=days),
        )
    )).scalars())
    metrics = await _latest_metrics(db, posts)
    # The question text behind a post that was published to answer one.
    question_refs = [
        int(post.cause_ref) for post in posts
        if post.cause_type == "AUDIENCE_QUESTION" and (post.cause_ref or "").isdigit()
    ]
    question_ids: dict[int, str] = {}
    if question_refs:
        rows = (await db.execute(
            select(ThreadsInteraction).where(ThreadsInteraction.id.in_(question_refs))
        )).scalars()
        question_ids = {row.id: (row.message or "") for row in rows}

    by_cause: dict[str, dict] = {}
    by_theme: dict[str, dict] = {}
    by_format: dict[str, dict] = {}
    # Recruitment outcomes. Reach is not the goal: a post with a thousand
    # views and no candidate is worth less than a post with two applicants.
    signals_by_post: dict[int, int] = {}
    candidates_by_post: dict[int, int] = {}
    interactions = {
        row.id: row for row in (await db.execute(select(ThreadsInteraction))).scalars()
    }
    signal_rows = list((await db.execute(select(CandidateSignal))).scalars())
    candidate_signal_ids = {
        row.source_signal_id for row in (await db.execute(select(Candidate))).scalars()
        if row.source_signal_id
    }
    for signal in signal_rows:
        if signal.status == CandidateSignalStatus.IGNORED:
            continue
        interaction = interactions.get(signal.interaction_id)
        if interaction is None or interaction.post_id is None or interaction.ignored:
            continue
        signals_by_post[interaction.post_id] = signals_by_post.get(interaction.post_id, 0) + 1
        if signal.id in candidate_signal_ids:
            candidates_by_post[interaction.post_id] = candidates_by_post.get(interaction.post_id, 0) + 1
    for post in posts:
        metric = metrics.get(post.id)
        response_rate = _response_rate(post, metric)
        views = int(metric.views) if metric is not None and metric.views is not None else None
        replies = int(metric.replies) if metric is not None and metric.replies is not None else None
        age_days = None
        if post.published_at is not None:
            published = post.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age_days = max(0, (now - published).days)
        theme = post.content_subject or theme_of(post.content or "")
        content_type = str(getattr(post.content_type, "value", post.content_type))
        post_signals = signals_by_post.get(post.id, 0)
        post_candidates = candidates_by_post.get(post.id, 0)
        for bucket, key in ((by_theme, theme), (by_format, content_type)):
            item = bucket.setdefault(key, {"name": key, "posts": 0, "measured": 0, "views": [], "replies": [], "ages": [], "signals": 0, "candidates": 0})
            if response_rate is not None:
                item.setdefault("response_rates", []).append(response_rate)
            item["posts"] += 1
            item["signals"] += post_signals
            item["candidates"] += post_candidates
            if views is not None:
                item["measured"] += 1
                item["views"].append(views)
                item["replies"].append(replies or 0)
                if age_days is not None:
                    item["ages"].append(age_days)
        cause_key = f"{post.cause_type or 'THEME'}:{post.cause_ref or theme}"
        item = by_cause.setdefault(cause_key, {
            "cause_type": post.cause_type or "THEME",
            "cause_ref": post.cause_ref or theme,
            "theme": theme, "posts": 0, "measured": 0, "views": [], "replies": [],
            "signals": 0, "candidates": 0,
            "question": question_ids.get(post.cause_ref) if post.cause_type == "AUDIENCE_QUESTION" else None,
        })
        item["posts"] += 1
        item["signals"] += post_signals
        item["candidates"] += post_candidates
        if views is not None:
            item["measured"] += 1
            item["views"].append(views)
            item["replies"].append(replies or 0)

    def finish(bucket: dict) -> list[dict]:
        rows = []
        for item in bucket.values():
            row = {key: value for key, value in item.items() if key not in ("views", "replies", "ages", "response_rates")}
            rates = item.get("response_rates", [])
            row["response_sample"] = len(rates)
            row["median_response_rate"] = round(median(rates), 2) if rates else None
            row["response_evidence"] = "comparable" if len(rates) >= 3 else "exploratory"
            row["median_views"] = _median(item["views"])
            row["median_replies"] = _median(item["replies"])
            row["median_age_days"] = _median(item.get("ages", []))
            rows.append(row)
        # Recruitment outcome first, reach second. A post that brought
        # candidates is worth more than a post that only collected views.
        rows.sort(key=lambda row: (-row.get("candidates", 0), -row.get("signals", 0), -(row["median_views"] or 0)))
        return rows

    themes = finish(by_theme)
    formats = finish(by_format)
    causes = finish(by_cause)
    return {
        "window_days": days,
        "by_theme": themes,
        "by_format": formats,
        "by_cause": causes,
        "best_theme": themes[0] if themes else None,
        "measured_posts": sum(row["measured"] for row in themes),
        "recommendation": _recommendation(themes),
        "limitations": [
            "Действия на 100 просмотров не равны уникальным людям; сравниваются замеры в возрасте 24 часа–7 дней, менее трёх постов — разведочная выборка.",
            "Постов с метриками мало: медиана по нескольким публикациям — ориентир, а не доказанный вывод.",
            "Просмотры растут с возрастом поста, поэтому темы сравнимы только внутри близкого окна публикации.",
            "Отсутствующие метрики не считаются нулевым охватом.",
        ],
    }


def _recommendation(themes: list[dict]) -> str:
    # The ranking that matters for a recruitment account: candidates first,
    # then signals, then reach.
    recruiting = [row for row in themes if row.get("candidates") or row.get("signals")]
    if recruiting:
        best = recruiting[0]
        return (
            f"В сохранённых данных интерес наблюдался к теме «{best['name']}»: сигналов {best.get('signals', 0)}, "
            f"подтверждённых кандидатов {best.get('candidates', 0)} при {best['posts']} публикациях. "
            "Это гипотеза для следующего теста, а не доказательство предпочтения аудитории. "
            "Отсутствие обращений не доказывает бесполезность темы."
        )
    measured = [row for row in themes if row["median_views"]]
    if len(measured) < 2:
        return "Пока слишком мало публикаций с метриками, чтобы сравнивать темы. Продолжайте публиковать разные темы и вернитесь к выводу через несколько недель."
    best = measured[0]
    worst = measured[-1]
    return (
        f"Кандидатов по темам пока нет — сравнение только по охвату. Лучше всего отрабатывает тема «{best['name']}» "
        f"(медиана {best['median_views']} просмотров, {best['posts']} публикаций), хуже всего «{worst['name']}» "
        f"(медиана {worst['median_views']})."
    )


def evidence_brief(evidence: dict | None, limit: int = 4) -> str:
    """Compact form for the planner prompt."""
    if not evidence:
        return ""
    lines = []
    for row in (evidence.get("by_theme") or [])[:limit]:
        if not row.get("median_views") and not row.get("signals"):
            continue
        lines.append(
            f"- {row['name']}: публикаций {row['posts']}, сигналов кандидатов {row.get('signals', 0)}, "
            f"подтверждённых кандидатов {row.get('candidates', 0)}, медиана {row.get('median_views')} просмотров; "
            f"действий на 100 просмотров {row.get('median_response_rate')}, "
            f"постов с сопоставимым возрастом замера {row.get('response_sample', 0)} "
            f"(24ч–7д; менее 3 — гипотеза, не победитель)"
        )
    if not lines:
        return ""
    return (
        "Что уже отработало по кандидатам и охвату (выборка мала — это ориентир, не доказанный вывод):\n"
        + "\n".join(lines)
    )
