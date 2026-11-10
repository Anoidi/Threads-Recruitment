"""Observed account performance -> bounded editorial tests, never ranking folklore.

Use a fixed first-day observation window, not each post's latest lifetime total.
No new provider requests, publishing permissions or model training happen here.
"""
from datetime import datetime, timedelta, timezone
from statistics import median
import re

from sqlalchemy import select

from .enums import MetricsStatus, PostStatus
from .models import Candidate, CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost

FIELDS = ("views", "likes", "replies", "reposts", "quotes")
MIN_POSTS = 3
WINDOW_START = 24
WINDOW_END = 30


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def opening_style(content):
    opening = re.split(r"(?<=[.!?…])\s+|\n", (content or "").strip(), maxsplit=1)[0][:160]
    if "?" in opening:
        return "question", "Вопрос в начале"
    if re.search(r"\d", opening):
        return "number", "Число в начале"
    return "statement", "Утверждение в начале"


def study_posts(posts, snapshots, *, now=None, days=30, outcomes=None):
    """One valid snapshot at 24–30h per live post; language-separated comparisons."""
    now = utc(now or datetime.now(timezone.utc))
    outcomes = outcomes or {}
    eligible = {
        p.id: p for p in posts
        if p.status == PostStatus.PUBLISHED and p.remote_id and not p.remote_id.startswith("mock-")
        and p.published_at and now - timedelta(days=days) <= utc(p.published_at) <= now
    }
    selected = {}
    for metric in snapshots:
        post = eligible.get(metric.post_id)
        if post is None or post.metrics_status == MetricsStatus.UNAVAILABLE or not metric.captured_at:
            continue
        captured = utc(metric.captured_at)
        age = (captured - utc(post.published_at)).total_seconds() / 3600
        values = [getattr(metric, name, None) for name in FIELDS]
        if captured > now or not WINDOW_START <= age <= WINDOW_END:
            continue
        if any(v is None or v < 0 for v in values):
            continue
        # Retain measured zero reach, but never divide by zero or count it as missing.
        previous = selected.get(post.id)
        if previous is None or (captured, metric.id or 0) < (utc(previous.captured_at), previous.id or 0):
            selected[post.id] = metric

    coverage = {"published": len(eligible), "comparable": len(selected), "pending": 0,
                "missing_window": 0, "unavailable": 0, "zero_views": 0}
    groups = {}
    examples = []
    for post in eligible.values():
        metric = selected.get(post.id)
        if metric is None:
            reason = "unavailable" if post.metrics_status == MetricsStatus.UNAVAILABLE else (
                "pending" if now - utc(post.published_at) < timedelta(hours=WINDOW_END) else "missing_window")
            coverage[reason] += 1
            continue
        coverage["zero_views"] += int(metric.views == 0)
        kind = str(getattr(post.content_type, "value", post.content_type))
        opening_key, opening_label = opening_style(post.content)
        values = {
            "views": metric.views,
            "reply_rate": 1000 * metric.replies / metric.views if metric.views else None,
            "action_rate": 1000 * sum(getattr(metric, name) for name in FIELDS[1:]) / metric.views if metric.views else None,
        }
        attributed = outcomes.get(post.id, {})
        for dimension, key, label in (("format", kind, kind), ("opening", opening_key, opening_label)):
            group = groups.setdefault((post.language, dimension, key), {
                "language": post.language, "dimension": dimension, "key": key, "label": label,
                "posts": 0, "views": [], "reply_rate": [], "action_rate": [], "signals": 0, "candidates": 0,
            })
            group["posts"] += 1
            for name, value in values.items():
                if value is not None:
                    group[name].append(value)
            group["signals"] += attributed.get("signals", 0)
            group["candidates"] += attributed.get("candidates", 0)
        examples.append({"post_id": post.id, "language": post.language, "format": kind,
                         "opening": opening_label, "views": metric.views,
                         "reply_rate": round(values["reply_rate"], 2) if values["reply_rate"] is not None else None,
                         "signals": attributed.get("signals", 0), "candidates": attributed.get("candidates", 0)})

    rows = []
    for group in groups.values():
        rates = group["reply_rate"]
        row = {key: value for key, value in group.items() if key not in ("views", "reply_rate", "action_rate")}
        row.update(median_views=round(median(group["views"]), 1),
                   median_reply_rate=round(median(rates), 2) if rates else None,
                   median_action_rate=round(median(group["action_rate"]), 2) if group["action_rate"] else None,
                   rate_sample=len(rates), evidence="exploratory")
        rows.append(row)
    rows.sort(key=lambda r: (r["language"], r["dimension"], r["key"]))

    tests = []
    for language in sorted({r["language"] for r in rows}):
        for dimension in ("format", "opening"):
            peers = [r for r in rows if r["language"] == language and r["dimension"] == dimension]
            mature = [r for r in peers if r["rate_sample"] >= MIN_POSTS]
            if len(mature) < 2:
                tests.append({"language": language, "dimension": dimension, "status": "collect",
                              "preferred": None, "comparison": None,
                              "reason": "Нужно минимум по 3 поста двух вариантов с ненулевым охватом в окне 24–30ч."})
                continue
            best = max(mature, key=lambda r: (r["median_reply_rate"], r["median_action_rate"], r["median_views"]))
            other = min((r for r in mature if r is not best), key=lambda r: (r["median_reply_rate"], r["median_action_rate"], r["median_views"]))
            # Views alone must not turn a silent post into a conversation winner.
            advantage = best["median_reply_rate"] > other["median_reply_rate"] and best["median_reply_rate"] > 0
            tests.append({"language": language, "dimension": dimension,
                          "status": "test" if advantage else "inconclusive",
                          "preferred": best["key"] if advantage else None,
                          "comparison": other["key"] if advantage else None,
                          "reason": (
                              f"Проверить «{best['label']}» против «{other['label']}»: медиана ответов на 1000 просмотров "
                              f"{best['median_reply_rate']} против {other['median_reply_rate']}; выборки {best['rate_sample']} и {other['rate_sample']}."
                              if advantage else "Разницы по ответам пока нет. Проверить другую подачу без увеличения частоты."
                          )})

            if not advantage:
                # Sparse successes should suggest what to explore, not vanish behind
                # the median zero of formats that were repeated many times.
                promising = [r for r in peers if 0 < r["rate_sample"] < MIN_POSTS and r["median_reply_rate"] > 0]
                if promising:
                    probe = max(promising, key=lambda r: (r["median_reply_rate"], r["median_views"]))
                    baseline = max(mature, key=lambda r: r["posts"])
                    tests[-1].update(status="explore", preferred=probe["key"], comparison=baseline["key"],
                        reason=f"Добрать тест «{probe['label']}» против «{baseline['label']}» на одной теме: "
                               f"пока только {probe['rate_sample']} пост(а), медиана ответов/1000 просмотров "
                               f"{probe['median_reply_rate']}. Это разведка, не победитель.")

    if coverage["comparable"] == 0:
        recommendation = "Нет сопоставимых замеров первых суток. Сначала проверить сбор insights и дождаться окна 24–30ч; генерация дополнительных постов не исправит отсутствие измерений."
    elif any(t["status"] == "test" for t in tests):
        recommendation = "Проверить предложенную подачу в следующем плане; сохранить альтернативу для сравнения. Просмотры и ответы сопоставлять отдельно с подтверждёнными кандидатами."
    else:
        recommendation = "Пока нет устойчивой гипотезы по подаче. Проверить два варианта на одной теме и языке; не повторять шаблон и не наращивать объём ради просмотров."
    examples.sort(key=lambda r: (-r["candidates"], -r["signals"], -(r["reply_rate"] or 0), -r["views"], r["post_id"]))
    return {"window_days": days, "measurement_window": "24–30ч", "minimum_per_variant": MIN_POSTS,
            "coverage": coverage, "groups": rows, "next_tests": tests, "examples": examples[:6],
            "recommendation": recommendation, "limitations": [
                "Наблюдаемая связь, не восстановленный алгоритм Threads и не доказательство причинности.",
                "Темы, время публикации и состав аудитории могут отличаться; меняйте один фактор за тест.",
                "Ответы и действия на 1000 просмотров не равны уникальным людям или заявкам.",
                "Кандидаты и сигналы показаны отдельно за первые 7 суток; незрелые посты ещё могут их получить.",
            ]}


async def growth_report(db, *, days=30):
    now = datetime.now(timezone.utc)
    posts = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.PUBLISHED, ThreadsPost.remote_id.is_not(None),
        ~ThreadsPost.remote_id.like("mock-%"), ThreadsPost.published_at >= now - timedelta(days=days),
    ))).scalars())
    ids = [p.id for p in posts]
    metrics = list((await db.execute(select(PostMetric).where(PostMetric.post_id.in_(ids)))).scalars()) if ids else []
    outcomes = {}
    if ids:
        links = (await db.execute(select(ThreadsInteraction, CandidateSignal, Candidate)
            .join(CandidateSignal, CandidateSignal.interaction_id == ThreadsInteraction.id)
            .outerjoin(Candidate, Candidate.source_signal_id == CandidateSignal.id)
            .where(ThreadsInteraction.post_id.in_(ids), ThreadsInteraction.ignored.is_(False),
                   CandidateSignal.status != "IGNORED"))).all()
        by_id = {p.id: p for p in posts}
        seen_signals, seen_candidates = set(), set()
        for interaction, signal, candidate in links:
            published = utc(by_id[interaction.post_id].published_at)
            end = min(now, published + timedelta(days=7))
            item = outcomes.setdefault(interaction.post_id, {"signals": 0, "candidates": 0})
            if signal.id not in seen_signals and published <= utc(signal.created_at) <= end:
                item["signals"] += 1
                seen_signals.add(signal.id)
            if (candidate and candidate.id not in seen_candidates and candidate.confirmed_at
                    and candidate.contact_value and candidate.consent_given
                    and published <= utc(candidate.confirmed_at) <= end):
                item["candidates"] += 1
                seen_candidates.add(candidate.id)
    return study_posts(posts, metrics, now=now, days=days, outcomes=outcomes)


def growth_brief(report, *, language=None):
    if not report:
        return ""
    tests = [t for t in report.get("next_tests", []) if language is None or t["language"] == language]
    groups = [r for r in report.get("groups", []) if r["dimension"] == "format"
              and (language is None or r["language"] == language)]
    groups.sort(key=lambda r: (-r["posts"], r["key"]))
    samples = "\n".join(
        f"{r['label']}: {r['posts']} постов, медиана просмотров {r['median_views']}, "
        f"ответов/1000 {r['median_reply_rate']}, подтверждённых кандидатов {r['candidates']}."
        for r in groups[:4]
    )
    return (report["recommendation"] + "\n" + "\n".join(t["reason"] for t in tests[:4])
            + "\n" + samples
            + "\nСравнение 24–30ч. Это гипотезы; не копируй посты, не выдумывай личные истории и не используй engagement bait.")
