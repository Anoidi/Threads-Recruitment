"""Read-only audience profile: what the audience asks, what we answered, what is missing.

Why this exists
---------------
The content pipeline had no memory of its audience. It used the last few
comments as loose inspiration, so it kept writing about the same condition
while the questions people actually asked went unanswered. This module turns
the captured replies and candidate signals into a comparison between demand
and coverage, and that comparison feeds the planner and the analyst.

Everything here is read-only and reports its own sample size. A small
sample must not look like a proven preference.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select

from .content_subjects import theme_of
from .enums import CandidateSignalStatus, PostStatus
from .models import CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost

# Editorial angles. A vacancy card is the job ad itself, not an answer to a
# question, so it does not count as coverage of a theme.
EDITORIAL_TYPES = {
    "FAQ", "EDUCATIONAL", "SALARY_DISCUSSION", "HOUSING_DISCUSSION",
    "MYTH_BUSTING", "QUESTION", "ENGAGEMENT", "TREND_RESPONSE",
    "RECRUITMENT_ADVICE", "DOCUMENTS_INFORMATION", "LEGAL_INFORMATION",
    "BEHIND_THE_SCENES", "RECRUITER_STORY", "CANDIDATE_STORY",
}

THEME_LABELS = {
    "HOUSING": "жильё",
    "PAY": "ставку и оплату",
    "SCHEDULE": "график и смены",
    "CONTRACT": "договор",
    "CITY": "город и дорогу",
    "TRANSPORT": "транспорт",
    "REQUIREMENTS": "требования к кандидату",
    "DOCUMENTS": "документы",
    "JOB_SEARCH": "поиск работы",
    "GENERAL": "общие вопросы",
    "COMPLAINT": "жалобы",
}

# A comment is not a question. Only real asks count as demand; everything
# else is engagement volume and must not push a theme to the top.
QUESTION_CLASSIFICATIONS = {
    "HOUSING_QUESTION", "SALARY_QUESTION", "SCHEDULE_QUESTION",
    "DOCUMENTS_QUESTION", "JOB_SEARCH",
}


def is_question(text: str, classification: str | None) -> bool:
    """True when the message is a real question about a condition."""
    label = (classification or "").upper()
    if label in QUESTION_CLASSIFICATIONS:
        return True
    if label == "COMPLAINT":
        return False
    value = (text or "").strip()
    if "?" in value:
        # The keyword classifier marks most real questions as OTHER, so a
        # question mark plus a detectable theme is a question.
        return bool(theme_of(value) != "GENERAL")
    return False


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


async def audience_profile(db, *, days: int = 30) -> dict:
    """Compare what the audience asks with what the account has published."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    interactions = list((await db.execute(
        select(ThreadsInteraction).where(ThreadsInteraction.occurred_at >= since, ThreadsInteraction.ignored.is_(False))
        .order_by(ThreadsInteraction.occurred_at.desc())
    )).scalars())
    signals = list((await db.execute(select(CandidateSignal).where(
        CandidateSignal.status != CandidateSignalStatus.IGNORED,
        CandidateSignal.created_at >= since,
        CandidateSignal.interaction_id.not_in(select(ThreadsInteraction.id).where(ThreadsInteraction.ignored.is_(True))),
    ))).scalars())
    posts = list((await db.execute(
        select(ThreadsPost).where(
            ThreadsPost.status == PostStatus.PUBLISHED,
            ThreadsPost.remote_id.is_not(None),
            ~ThreadsPost.remote_id.like("mock-%"),
        )
    )).scalars())
    metrics = await _latest_metrics(db, posts)

    # Demand: the questions the audience actually asked. Other comments are
    # engagement, not demand.
    demand: dict[str, dict] = {}
    engagement = 0
    counted_questions = set()
    for interaction in interactions:
        text = " ".join((interaction.message or "").split())
        if not is_question(interaction.message, interaction.classification):
            engagement += 1
            continue
        counted_questions.add(interaction.id)
        theme = theme_of(interaction.message, interaction.classification)
        item = demand.setdefault(theme, {
            "theme": theme, "label": THEME_LABELS.get(theme, theme),
            "asked": 0, "from_interactions": 0, "from_signals": 0,
            "last_asked_at": None, "examples": [], "on_our_posts": 0,
        })
        item["asked"] += 1
        item["from_interactions"] += 1
        if interaction.post_id:
            item["on_our_posts"] += 1
        stamp = interaction.occurred_at
        if stamp is not None and (item["last_asked_at"] is None or stamp > item["last_asked_at"]):
            item["last_asked_at"] = stamp
        if text and len(item["examples"]) < 3 and text not in item["examples"]:
            item["examples"].append(text[:160])
    for signal in signals:
        # A classification of the same question is not a second person asking.
        if signal.interaction_id in counted_questions:
            continue
        theme = theme_of("", signal.intent)
        item = demand.setdefault(theme, {
            "theme": theme, "label": THEME_LABELS.get(theme, theme),
            "asked": 0, "from_interactions": 0, "from_signals": 0,
            "last_asked_at": None, "examples": [], "on_our_posts": 0,
        })
        item["asked"] += 1
        item["from_signals"] += 1

    # Coverage: editorial publications per theme, with their measured result.
    coverage: dict[str, dict] = {}
    for post in posts:
        if str(post.content_type.value if hasattr(post.content_type, "value") else post.content_type) not in EDITORIAL_TYPES:
            continue
        theme = post.content_subject or theme_of(post.content or "")
        item = coverage.setdefault(theme, {
            "theme": theme, "label": THEME_LABELS.get(theme, theme),
            "published": 0, "views": [], "replies": [], "last_published_at": None,
        })
        item["published"] += 1
        metric = metrics.get(post.id)
        if metric is not None and metric.views is not None:
            item["views"].append(int(metric.views))
        if metric is not None and metric.replies is not None:
            item["replies"].append(int(metric.replies))
        if post.published_at and (item["last_published_at"] is None or post.published_at > item["last_published_at"]):
            item["last_published_at"] = post.published_at

    for item in coverage.values():
        item["median_views"] = int(median(item["views"])) if item["views"] else None
        item["median_replies"] = int(median(item["replies"])) if item["replies"] else None
        item.pop("views", None)
        item.pop("replies", None)

    demands = sorted(demand.values(), key=lambda item: (-item["asked"], item["theme"]))
    coverages = sorted(coverage.values(), key=lambda item: (-item["published"], item["theme"]))
    published_by_theme = {item["theme"]: item["published"] for item in coverages}

    # A theme the audience keeps asking about and we never answer.
    gaps = [
        {
            "theme": item["theme"], "label": item["label"], "asked": item["asked"],
            "published": published_by_theme.get(item["theme"], 0),
            "deficit": item["asked"] - published_by_theme.get(item["theme"], 0),
            "last_asked_at": item["last_asked_at"].isoformat() if item["last_asked_at"] else None,
        }
        for item in demands
        if item["asked"] >= 2 and published_by_theme.get(item["theme"], 0) < item["asked"]
    ]
    # A theme we keep publishing although nobody asked about it.
    served = [
        {"theme": item["theme"], "label": item["label"], "published": item["published"], "asked": 0}
        for item in coverages
        if published_by_theme.get(item["theme"], 0) >= 2 and demand.get(item["theme"], {}).get("asked", 0) == 0
    ]

    recommendation = _recommendation(demands, coverages, gaps)
    best = max(
        (item for item in coverages if item["median_views"]),
        key=lambda item: (item["median_views"] or 0, item["published"]),
        default=None,
    )

    return {
        "window_days": days,
        "generated_at": now.isoformat(),
        "sample": {
            "interactions": len(interactions),
            "questions": sum(item["asked"] for item in demands),
            "other_comments": engagement,
            "signals": len(signals),
            "published_posts": len(posts),
            "posts_with_metrics": len(metrics),
        },
        "demands": demands,
        "coverage": coverages,
        "gaps": gaps,
        "served": served,
        "best_performing_theme": (
            {"theme": best["theme"], "label": best["label"], "median_views": best["median_views"],
             "median_replies": best["median_replies"], "published": best["published"]}
            if best else None
        ),
        "recommendation": recommendation,
        "limitations": [
            "Профиль опирается на собранные обращения и сигналы, а не на весь трафик аккаунта.",
            "Темы определяются по маркерам в тексте; при малой выборке это ориентир, а не доказанный факт.",
            "Отсутствие метрик не считается нулевым охватом.",
        ],
    }


def _recommendation(demands: list[dict], coverages: list[dict], gaps: list[dict]) -> str:
    if not demands:
        return "Пока нет собранных вопросов аудитории. Профиль начнёт складываться после первых обращений и упоминаний."
    top = demands[0]
    if gaps:
        gap = gaps[0]
        return (
            f"Чаще всего спрашивают про {gap['label']} — {gap['asked']} раз, "
            f"а editorial-публикаций на эту тему {gap['published']}. Следующий приоритет — закрыть этот разрыв."
        )
    best = max(
        (item for item in coverages if item["median_views"]),
        key=lambda item: item["median_views"] or 0,
        default=None,
    )
    if best:
        return (
            f"Чаще всего спрашивают про {top['label']} ({top['asked']} раз). "
            f"Лучше всего по охвату отрабатывает тема «{best['label']}» — медиана {best['median_views']} просмотров."
        )
    return f"Чаще всего спрашивают про {top['label']} — {top['asked']} раз. Метрик по темам пока мало."


def audience_brief(profile: dict, limit: int = 5) -> str:
    """Compact form for a model prompt."""
    lines: list[str] = []
    demands = profile.get("demands") or []
    for item in demands[:limit]:
        lines.append(f"- {item['label']}: спросили {item['asked']} раз")
    if not lines:
        return "Обращения аудитории пока не собраны."
    covered = []
    for item in (profile.get("coverage") or [])[:limit]:
        covered.append(f"- {item['label']}: опубликовано {item['published']}")
    text = "Что спрашивали у аудитории за последний период:\n" + "\n".join(lines)
    if covered:
        text += "\nЧто уже отвечено:\n" + "\n".join(covered)
    gaps = profile.get("gaps") or []
    if gaps:
        text += "\nТемы без ответа (приоритет): " + ", ".join(item["label"] for item in gaps[:3])
    return text
