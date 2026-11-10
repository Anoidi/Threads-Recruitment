"""Read-only editorial evidence shared by API, analyst and planner."""
from datetime import datetime, timezone
from sqlalchemy import func, select
from .models import ThreadsPost, PostMetric, PostApproval
import re
from .enums import MetricsStatus, PostStatus


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# The fields that must all be present and non-negative for a snapshot to be
# comparable. One missing or corrupt field would otherwise compare a fresh
# post against a broken measurement.
COMPARABLE_FIELDS = ("views", "likes", "replies", "reposts", "quotes")


def comparable_metric_conditions():
    from sqlalchemy import and_
    conditions = []
    for field in COMPARABLE_FIELDS:
        attribute = getattr(PostMetric, field)
        conditions.append(attribute.is_not(None))
        conditions.append(attribute >= 0)
    return [and_(*conditions)]


async def latest_metrics(db, post_ids):
    """The newest snapshot per post (audit I-8/D7).

    The old analytics loaded *every* snapshot of *every* post into Python and
    picked the newest per post there - the table grows linearly with data
    collection, so that query would keep getting heavier forever. A window
    function keeps the same choice (latest captured_at, then latest id) while
    fetching only the selected rows.
    """
    ids = [item for item in post_ids if item is not None]
    if not ids:
        return {}
    ranked = func.row_number().over(
        partition_by=PostMetric.post_id,
        order_by=(PostMetric.captured_at.desc(), PostMetric.id.desc()),
    ).label("rn")
    newest = select(PostMetric.id.label("metric_id"), ranked).where(PostMetric.post_id.in_(ids)).subquery()
    rows = (await db.execute(
        select(PostMetric).join(newest, (newest.c.metric_id == PostMetric.id) & (newest.c.rn == 1))
    )).scalars()
    return {metric.post_id: metric for metric in rows}


def analyze_content(posts, snapshots, candidate_counts=None):
    candidate_counts = candidate_counts or {}
    latest = {}
    for metric in sorted(snapshots, key=lambda m: (_utc(m.captured_at), m.id or 0)):
        latest[metric.post_id] = metric
    # Posts whose insights the platform can no longer return are excluded from
    # the comparison. Their last snapshot is stale, so keeping them would
    # compare a fresh post against an eleven-day-old measurement.
    unavailable = {post.id for post in posts if getattr(post, 'metrics_status', None) == MetricsStatus.UNAVAILABLE}
    eligible = [
        p for p in posts
        if p.status == PostStatus.PUBLISHED and p.remote_id and not p.remote_id.startswith('mock-') and p.id not in unavailable
    ]
    groups = {}
    measured = 0
    comparable = 0
    for post in eligible:
        metric = latest.get(post.id)
        if metric is None:
            continue
        measured += 1
        if not post.published_at or metric.views is None or metric.views <= 0:
            continue
        actions = (metric.likes, metric.replies, metric.reposts, metric.quotes)
        if any(value is None or value < 0 for value in actions):
            continue
        age = (_utc(metric.captured_at) - _utc(post.published_at)).total_seconds() / 3600
        if age < 24:
            continue
        bucket = '24–48h' if age < 48 else '48h–7d' if age < 168 else '7d+'
        kind = getattr(post.content_type, 'value', str(post.content_type))
        key = (kind, post.language, bucket)
        group = groups.setdefault(key, dict(content_type=kind, language=post.language, age_bucket=bucket, posts=0, views=0, actions=0, candidate_signals=0, candidates=0))
        group['posts'] += 1
        group['views'] += metric.views
        group['actions'] += sum(actions)
        attributed = candidate_counts.get(post.id, {})
        group['candidate_signals'] += int(attributed.get('signals', 0))
        group['candidates'] += int(attributed.get('candidates', 0))
        comparable += 1
    cohorts = list(groups.values())
    for group in cohorts:
        group['engagement_rate'] = round(100 * group['actions'] / group['views'], 2)
        group['candidate_rate'] = round(100 * group['candidates'] / group['views'], 4) if group['views'] else 0
        group['message_rate'] = round(100 * group['candidate_signals'] / group['views'], 4) if group['views'] else 0
        group['evidence'] = 'limited' if group['posts'] < 3 else 'exploratory'
    hypotheses = []
    for group in cohorts:
        peers = [g for g in cohorts if g['language'] == group['language'] and g['age_bucket'] == group['age_bucket'] and g['posts'] >= 3]
        if group['posts'] >= 3 and len(peers) >= 2 and group is max(peers, key=lambda g: g['engagement_rate']):
            hypotheses.append(f"Гипотеза: проверить ещё 2 поста {group['content_type']} ({group['language']}); наблюдаемая вовлечённость {group['engagement_rate']}%, окно {group['age_bucket']}. Это не доказательство причинности.")
    recommendation = ' '.join(hypotheses) or 'Недостаточно сопоставимых данных: сравните минимум по 3 поста двух форматов одного языка в одинаковом возрастном окне. Посты младше 24 часов не оцениваем.'
    best_candidates = max(cohorts, key=lambda item: (item.get('candidates', 0), item.get('candidate_rate', 0)), default=None)
    return dict(published_posts=len(eligible), posts_with_metrics=measured, comparable_posts=comparable, cohorts=cohorts, hypotheses=hypotheses, recommendation=recommendation, insufficient_data=not hypotheses, best_for_candidates=best_candidates, posts_metrics_unavailable=len(unavailable), limitations=['Сигналы кандидатов не равны подтверждённым обращениям или найму.', 'Отсутствующие метрики не считаются нулями.', 'Посты, метрики которых платформа больше не отдаёт, исключены из сравнения.', 'Возраст определяется на момент последнего замера; окна приблизительные.'])


async def editorial_evidence(db):
    from .editorial_memory import approved_rules
    from .audience_profile import audience_profile
    from .content_evidence import content_evidence
    from .recruitment_intelligence import vacancy_demand
    posts = list((await db.execute(select(ThreadsPost).order_by(ThreadsPost.created_at.desc()).limit(200))).scalars())
    ids = [p.id for p in posts]
    metrics = list((await db.execute(select(PostMetric).where(PostMetric.post_id.in_(ids)))).scalars()) if ids else []
    from .models import Candidate, CandidateSignal, ThreadsInteraction
    attributed = {}
    if ids:
        rows = await db.execute(select(ThreadsInteraction.post_id, CandidateSignal.id, Candidate.id).join(CandidateSignal, CandidateSignal.interaction_id == ThreadsInteraction.id).outerjoin(Candidate, Candidate.id == CandidateSignal.candidate_id).where(ThreadsInteraction.post_id.in_(ids)))
        for post_id, signal_id, candidate_id in rows.all():
            item = attributed.setdefault(post_id, {'signals': 0, 'candidates': 0})
            item['signals'] += 1
            item['candidates'] += int(candidate_id is not None)
    report = analyze_content(posts, metrics, attributed)
    reviews = list((await db.execute(select(PostApproval).where(
        PostApproval.post_id.in_(ids),
        PostApproval.actor == 'local-operator',
    ).order_by(PostApproval.created_at.desc(), PostApproval.id.desc()).limit(30))).scalars()) if ids else []
    # Deduplicate repeated decisions for the same version. Automatic approvals
    # are not evidence of the operator's taste or factual verification.
    seen = set()
    feedback = []
    by_id = {p.id: p for p in posts}
    for review in reviews:
        key = (review.post_id, review.post_version)
        if key in seen:
            continue
        seen.add(key)
        post = by_id[review.post_id]
        note = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|@\w+|\+?\d[\d ()-]{7,}\d', '[redacted]', review.note or '')[:160]
        feedback.append(dict(decision=review.decision, content_type=getattr(post.content_type, 'value', str(post.content_type)), language=post.language, note=note, post_version=review.post_version))
        if len(feedback) == 8:
            break
    report['operator_feedback'] = feedback
    report['confirmed_editorial_preferences'] = await approved_rules(db)
    patterns = {}
    for item in feedback:
        note = ' '.join(item['note'].split()).strip()
        if not note or note == '[redacted]':
            continue
        key = (item['content_type'], item['language'], item['decision'], note.casefold())
        pattern = patterns.setdefault(key, dict(content_type=item['content_type'], language=item['language'],
            decision=item['decision'], note=note, occurrences=0))
        pattern['occurrences'] += 1
    report['repeated_editorial_preferences'] = sorted(
        [pattern for pattern in patterns.values() if pattern['occurrences'] >= 2],
        key=lambda pattern: -pattern['occurrences'],
    )[:4]
    report['learning_mode'] = 'editorial_feedback_not_model_training'
    report['recent_topics'] = [dict(content_type=getattr(p.content_type, 'value', str(p.content_type)), language=p.language, opening=p.content[:180]) for p in posts[:12]]
    # What the audience actually asked for, compared with what we published.
    # This is the part that lets the next post answer a real question.
    report['audience_profile'] = await audience_profile(db)
    # What actually worked, measured per cause, per theme and per format. This
    # is the part that lets the planner prefer a theme that resonates over one
    # that only repeats itself.
    report['content_evidence'] = await content_evidence(db)
    from .growth_learning import growth_report
    report['growth_learning'] = await growth_report(db)
    # Which active vacancies attract nobody. This is the most actionable
    # signal in the system: it names the exact job to talk about next.
    report['vacancy_demand'] = await vacancy_demand(db)
    # The format experiment: one confirmed fact, several formats, measured on
    # candidates. The analyst reports it with its sample sizes.
    from .experiments import current_report

    report['experiment'] = await current_report(db)
    return report


async def analytics_aggregates(db):
    """Day/segment aggregates for the analytics endpoint (audit I-8/D7).

    The old endpoint summed every metric in a Python loop over all loaded
    posts. The metric sums now come from one SQL GROUP BY over the latest
    snapshots only; per-post rows (ranking) and signal attribution stay in
    Python because the audit demanded a shared attribution rule, and that
    part is already SQL-backed in recruitment_intelligence.post_attribution.

    Returns ``(posts, latest, by_date, segment_map, ranked)`` with the same
    shapes the old inline loop produced - a golden test compares them.
    """
    from .recruitment_intelligence import post_attribution

    day_bucket = func.date(func.coalesce(ThreadsPost.published_at, ThreadsPost.created_at))
    validity = comparable_metric_conditions()
    ranked_snapshot = func.row_number().over(
        partition_by=PostMetric.post_id,
        order_by=(PostMetric.captured_at.desc(), PostMetric.id.desc()),
    ).label("rn")
    newest = (
        select(PostMetric.id.label("metric_id"), ranked_snapshot)
        .join(ThreadsPost, ThreadsPost.id == PostMetric.post_id)
        .where(
            ThreadsPost.status == PostStatus.PUBLISHED,
            ThreadsPost.remote_id.is_not(None),
            ThreadsPost.remote_id.notlike("mock-%"),
        )
        .subquery()
    )
    grouped = (await db.execute(
        select(
            day_bucket.label("day"),
            ThreadsPost.content_type.label("kind"),
            ThreadsPost.language.label("lang"),
            func.count(ThreadsPost.id).label("posts"),
            func.sum(PostMetric.views).label("views"),
            func.sum(PostMetric.likes).label("likes"),
            func.sum(PostMetric.replies).label("replies"),
            (func.coalesce(func.sum(PostMetric.likes), 0) + func.coalesce(func.sum(PostMetric.replies), 0)
             + func.coalesce(func.sum(PostMetric.reposts), 0) + func.coalesce(func.sum(PostMetric.quotes), 0)).label("interactions"),
        )
        .select_from(ThreadsPost)
        .join(PostMetric, PostMetric.post_id == ThreadsPost.id)
        .join(newest, (newest.c.metric_id == PostMetric.id) & (newest.c.rn == 1))
        .where(*validity)
        .group_by(day_bucket, ThreadsPost.content_type, ThreadsPost.language)
    )).mappings().all()

    posts = list((await db.execute(
        select(ThreadsPost).where(
            ThreadsPost.status == PostStatus.PUBLISHED,
            ThreadsPost.remote_id.is_not(None),
            ~ThreadsPost.remote_id.like("mock-%"),
        ).order_by(ThreadsPost.published_at, ThreadsPost.id)
    )).scalars())
    ids = [post.id for post in posts]
    latest = await latest_metrics(db, ids)
    attribution = await post_attribution(db, ids)
    signal_counts = {post_id: int(item["signals"]) for post_id, item in attribution.items()}
    candidate_counts = {post_id: int(item["candidates"]) for post_id, item in attribution.items()}

    by_date: dict[str, dict[str, int | str]] = {}
    segment_map: dict[tuple[str, str], dict[str, int | float | str]] = {}
    for row in grouped:
        day = str(row["day"])
        key = (str(row["kind"]), str(row["lang"]))
        views = int(row["views"] or 0)
        point = by_date.setdefault(day, {"date": day, "impressions": 0, "replies": 0, "signals": 0})
        point["impressions"] += views
        point["replies"] += int(row["replies"] or 0)
        segment = segment_map.setdefault(key, {
            "content_type": key[0], "language": key[1], "posts": 0,
            "views": 0, "likes": 0, "replies": 0, "interactions": 0, "signals": 0, "candidates": 0,
        })
        segment["posts"] += int(row["posts"] or 0)
        segment["views"] += views
        segment["likes"] += int(row["likes"] or 0)
        segment["replies"] += int(row["replies"] or 0)
        segment["interactions"] += int(row["interactions"] or 0)

    ranked: list[dict] = []
    for post in posts:
        metric = latest.get(post.id)
        signals = signal_counts.get(post.id, 0)
        stamp = post.published_at or post.created_at
        date_key = stamp.date().isoformat()
        if metric is None or any(getattr(metric, field) is None or getattr(metric, field) < 0 for field in COMPARABLE_FIELDS):
            # Candidate intent is collected independently of post insights.
            if signals:
                point = by_date.setdefault(date_key, {"date": date_key, "impressions": 0, "replies": 0, "signals": 0})
                point['signals'] += signals
            continue
        point = by_date.setdefault(date_key, {"date": date_key, "impressions": 0, "replies": 0, "signals": 0})
        point["signals"] += signals
        segment = segment_map.get((str(getattr(post.content_type, "value", post.content_type)), post.language))
        if segment is not None:
            segment["signals"] += signals
            segment["candidates"] += int(candidate_counts.get(post.id, 0))
        views = int(metric.views or 0)
        likes = int(metric.likes or 0)
        replies = int(metric.replies or 0)
        reposts = int(metric.reposts or 0)
        quotes = int(metric.quotes or 0)
        ranked.append({
            "id": str(post.id), "content": post.content, "impressions": views,
            "likes": likes, "replies": replies, "signals": signals,
            "score": signals * 10_000 + replies * 1_000 + likes * 10 + views,
        })

    sorted_days = sorted(by_date)
    return {
        "posts": posts,
        "latest": latest,
        "snapshots_for_analysis": list(latest.values()),
        "signal_counts": signal_counts,
        "candidate_counts": candidate_counts,
        "performance": [by_date[day] for day in sorted_days],
        "segment_map": segment_map,
        "ranked": ranked,
    }
