from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.enums import CandidateSignalStatus, InteractionKind, PostStatus
from app.models import Candidate, CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost, Topic


@pytest.mark.asyncio
@pytest.mark.parametrize('connection', ['disconnected', 'missing_scope', 'expired', 'ready'])
async def test_radar_api_does_not_treat_historical_success_as_current_access(client, db, connection):
    from app.models import SyncState, ThreadsAccount
    now = datetime.now(timezone.utc)
    db.add(SyncState(resource='threads_trend_radar', last_success_at=now))
    if connection != 'disconnected':
        db.add(ThreadsAccount(profile_id='radar-test', username='test', connected=True,
            is_mock=False, api_status='READY', access_token_encrypted='test',
            permissions=[] if connection == 'missing_scope' else ['threads_keyword_search'],
            token_expires_at=now-timedelta(days=1) if connection == 'expired' else None))
    await db.commit()
    response = await client.get('/api/threads/analytics')
    assert response.status_code == 200
    coverage = response.json()['coverage']
    assert coverage['public_threads'] == ('available' if connection == 'ready' else 'unavailable')
    assert bool(coverage['public_threads_detail']) == (connection != 'ready')


@pytest.mark.asyncio
async def test_unmeasured_posts_are_not_ranked_as_zero_performance(client, db):
    post = ThreadsPost(content='Unmeasured', content_type='FAQ', language='uk',
        status=PostStatus.PUBLISHED, remote_id='real-unmeasured', published_at=datetime.now(timezone.utc))
    db.add(post)
    await db.commit()
    data = (await client.get('/api/threads/analytics')).json()
    assert data['sample_size'] == 1
    assert data['top_posts'] == []
    assert data['segments'] == []
    assert data['performance'] == []


@pytest.mark.asyncio
async def test_analytics_uses_latest_metric_snapshot_and_exposes_trends(client, db):
    now = datetime.now(timezone.utc)
    first = ThreadsPost(
        content="Детальна вакансія у Кракові зі ставкою, графіком і житлом.",
        content_type="VACANCY",
        language="uk",
        status=PostStatus.PUBLISHED,
        remote_id="remote-1",
        published_at=now - timedelta(days=1),
    )
    second = ThreadsPost(
        content="Порада: які питання поставити рекрутеру перед виїздом.",
        content_type="EDUCATIONAL",
        language="uk",
        status=PostStatus.PUBLISHED,
        remote_id="remote-2",
        published_at=now,
    )
    db.add_all([first, second])
    await db.flush()
    db.add_all([
        PostMetric(post_id=first.id, views=100, likes=8, replies=2, reposts=1, quotes=0, captured_at=now - timedelta(hours=20)),
        PostMetric(post_id=first.id, views=180, likes=15, replies=5, reposts=2, quotes=1, captured_at=now - timedelta(hours=1)),
        PostMetric(post_id=second.id, views=70, likes=4, replies=1, reposts=0, quotes=0, captured_at=now),
    ])
    interaction = ThreadsInteraction(
        post_id=first.id,
        remote_id="reply-1",
        kind=InteractionKind.REPLY,
        username="candidate",
        message="Шукаю роботу",
    )
    db.add(interaction)
    await db.flush()
    db.add(CandidateSignal(interaction_id=interaction.id, username="candidate", intent="JOB_SEARCH", confidence=.9))
    db.add_all([
        Topic(title="Житло для працівників у Кракові", source="threads-keyword-search", priority=86,
              explanation="3 свіжі згадки", language="uk"),
        Topic(title="Зміни в umowa zlecenie", source="google-news-rss", priority=62,
              explanation="Новина для перевірки", language="pl"),
    ])
    await db.commit()

    response = await client.get("/api/threads/analytics")
    assert response.status_code == 200
    data = response.json()
    assert data["sample_size"] == 2
    assert sum(point["impressions"] for point in data["performance"]) == 250
    assert sum(point["replies"] for point in data["performance"]) == 6
    assert data["top_posts"][0]["id"] == str(first.id)
    assert data["top_posts"][0]["signals"] == 1
    assert data["coverage"]["posts_with_metrics"] == 2
    assert data["trends"][0]["source"] == "threads-keyword-search"


# ---------------------------------------------------------------------------
# Audit I-8/D7: the aggregation moved into SQL (window latest + GROUP BY).
# The golden test keeps the previous inline implementation as the reference
# and proves the endpoint still produces the same numbers on the same seed.
# ---------------------------------------------------------------------------

def _reference_analytics(posts, snapshots, attribution):
    """Verbatim copy of the previous inline aggregation from api.py."""
    latest = {}
    for snapshot in snapshots:
        latest[snapshot.post_id] = snapshot
    signal_counts = {post_id: int(item["signals"]) for post_id, item in attribution.items()}
    candidate_counts = {post_id: int(item["candidates"]) for post_id, item in attribution.items()}

    by_date = {}
    segment_map = {}
    ranked = []
    for post in posts:
        metric = latest.get(post.id)
        signals = int(signal_counts.get(post.id, 0))
        stamp = post.published_at or post.created_at
        date_key = stamp.date().isoformat()
        if metric is None or any(getattr(metric, field) is None or getattr(metric, field) < 0 for field in ('views', 'likes', 'replies', 'reposts', 'quotes')):
            if signals:
                point = by_date.setdefault(date_key, {"date": date_key, "impressions": 0, "replies": 0, "signals": 0})
                point['signals'] += signals
            continue
        views = int(metric.views or 0) if metric else 0
        likes = int(metric.likes or 0) if metric else 0
        replies = int(metric.replies or 0) if metric else 0
        reposts = int(metric.reposts or 0) if metric else 0
        quotes = int(metric.quotes or 0) if metric else 0
        point = by_date.setdefault(date_key, {"date": date_key, "impressions": 0, "replies": 0, "signals": 0})
        point["impressions"] += views
        point["replies"] += replies
        point["signals"] += signals
        segment_key = (str(getattr(post.content_type, "value", post.content_type)), post.language)
        segment = segment_map.setdefault(segment_key, {
            "content_type": segment_key[0], "language": segment_key[1], "posts": 0,
            "views": 0, "likes": 0, "replies": 0, "interactions": 0, "signals": 0, "candidates": 0,
        })
        segment["posts"] += 1
        segment["views"] += views
        segment["likes"] += likes
        segment["replies"] += replies
        segment["interactions"] += likes + replies + reposts + quotes
        segment["signals"] += signals
        segment["candidates"] += int(candidate_counts.get(post.id, 0))
        ranked.append({
            "id": str(post.id), "content": post.content, "impressions": views,
            "likes": likes, "replies": replies, "signals": signals,
            "score": signals * 10_000 + replies * 1_000 + likes * 10 + views,
        })

    segments = []
    for segment in segment_map.values():
        views = int(segment["views"])
        segment["engagement"] = round((int(segment["interactions"]) / views * 100), 2) if views else 0.0
        segment["candidate_rate"] = round(int(segment["candidates"]) / views * 100, 4) if views else 0.0
        segments.append(segment)
    segments.sort(key=lambda item: (float(item["engagement"]), int(item["views"])), reverse=True)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {
        "sample_size": len(posts),
        "performance": sorted(by_date.values(), key=lambda item: item["date"]),
        "segments": segments,
        "top_posts": [{key: value for key, value in item.items() if key != "score"} for item in ranked[:10]],
        "attributed": attributed_ref(signal_counts, candidate_counts),
    }


def attributed_ref(signal_counts, candidate_counts):
    return {post_id: {"signals": int(signal_counts.get(post_id, 0)), "candidates": int(candidate_counts.get(post_id, 0))} for post_id in set(signal_counts) | set(candidate_counts)}


@pytest.mark.asyncio
async def test_analytics_matches_previous_inline_aggregation(db, client):
    from sqlalchemy import select

    from app.content_analysis import analyze_content
    from app.recruitment_intelligence import post_attribution

    now = datetime.now(timezone.utc)
    valid = ThreadsPost(content="Валідний пост із двома замірами", content_type="VACANCY", language="uk",
                        status=PostStatus.PUBLISHED, remote_id="remote-gold-1",
                        published_at=now - timedelta(days=2))
    stale_only = ThreadsPost(content="Пост із зіпсованим останнім заміром", content_type="EDUCATIONAL", language="uk",
                             status=PostStatus.PUBLISHED, remote_id="remote-gold-2", published_at=now)
    measured = ThreadsPost(content="Другий валідний пост того ж дня", content_type="FAQ", language="pl",
                           status=PostStatus.PUBLISHED, remote_id="remote-gold-3", published_at=now)
    unmeasured = ThreadsPost(content="Пост без замірів", content_type="QUESTION", language="uk",
                             status=PostStatus.PUBLISHED, remote_id="remote-gold-4", published_at=now)
    mock = ThreadsPost(content="Мок-пост", content_type="VACANCY", language="uk",
                       status=PostStatus.PUBLISHED, remote_id="mock-gold", published_at=now)
    draft = ThreadsPost(content="Чернетка із заміром", content_type="VACANCY", language="uk",
                        status=PostStatus.DRAFT, remote_id="remote-gold-5")
    db.add_all([valid, stale_only, measured, unmeasured, mock, draft])
    await db.flush()
    db.add_all([
        PostMetric(post_id=valid.id, views=90, likes=5, replies=1, reposts=0, quotes=0,
                   captured_at=now - timedelta(hours=30)),
        PostMetric(post_id=valid.id, views=150, likes=12, replies=3, reposts=1, quotes=0,
                   captured_at=now - timedelta(hours=2)),
        PostMetric(post_id=stale_only.id, views=-5, likes=3, replies=1, reposts=0, quotes=0, captured_at=now),
        PostMetric(post_id=measured.id, views=70, likes=4, replies=2, reposts=0, quotes=1, captured_at=now),
        PostMetric(post_id=mock.id, views=10_000, likes=100, replies=20, reposts=5, quotes=5, captured_at=now),
        PostMetric(post_id=draft.id, views=500, likes=50, replies=10, reposts=2, quotes=2, captured_at=now),
    ])
    good_interaction = ThreadsInteraction(post_id=valid.id, remote_id="ig-1", kind=InteractionKind.REPLY,
                                          username="anna", message="Шукаю роботу")
    ignored_signal_interaction = ThreadsInteraction(post_id=valid.id, remote_id="ig-2", kind=InteractionKind.REPLY,
                                                    username="oleh", message="Ціни?")
    ignored_interaction = ThreadsInteraction(post_id=stale_only.id, remote_id="ig-3", kind=InteractionKind.REPLY,
                                             username="ignored-user", message="ignore me", ignored=True)
    db.add_all([good_interaction, ignored_signal_interaction, ignored_interaction])
    await db.flush()
    candidate = Candidate(name="Anna", threads_username="anna", contact_value="anna@example.com",
                          consent_given=True)
    db.add(candidate)
    await db.flush()
    db.add_all([
        CandidateSignal(interaction_id=good_interaction.id, username="anna", intent="JOB_SEARCH",
                        confidence=0.9, candidate_id=candidate.id),
        CandidateSignal(interaction_id=ignored_signal_interaction.id, username="oleh", intent="QUESTION",
                        confidence=0.5, status=CandidateSignalStatus.IGNORED),
    ])
    await db.commit()

    ids = [valid.id, stale_only.id, measured.id, unmeasured.id]
    posts = list((await db.execute(select(ThreadsPost).where(
        ThreadsPost.status == PostStatus.PUBLISHED,
        ThreadsPost.remote_id.is_not(None),
        ~ThreadsPost.remote_id.like("mock-%"),
    ).order_by(ThreadsPost.published_at, ThreadsPost.id))).scalars())
    snapshots = list((await db.execute(select(PostMetric).order_by(
        PostMetric.post_id, PostMetric.captured_at, PostMetric.id))).scalars())
    attribution = await post_attribution(db, [post.id for post in posts])
    reference = _reference_analytics(posts, snapshots, attribution)

    data = (await client.get("/api/threads/analytics")).json()

    assert data["sample_size"] == reference["sample_size"]
    assert sorted(data["performance"], key=lambda item: item["date"]) == reference["performance"]
    assert data["segments"] == reference["segments"]
    assert data["top_posts"] == reference["top_posts"]
    # Candidate accounting on the verified post: one signal, one candidate.
    assert data["top_posts"][0]["signals"] == 1
    # The editorial evidence must not change either: passing only the latest
    # snapshots is equivalent to passing every snapshot and re-picking the
    # newest per post.
    assert data["editorial_analysis"] == analyze_content(posts, snapshots, reference["attributed"])


@pytest.mark.asyncio
async def test_analytics_response_is_cached_only_within_ttl(db, client, monkeypatch):
    from app.config import settings

    now = datetime.now(timezone.utc)
    post = ThreadsPost(content="Кеш-пост", content_type="VACANCY", language="uk",
                       status=PostStatus.PUBLISHED, remote_id="remote-cache", published_at=now)
    db.add(post)
    await db.flush()
    metric = PostMetric(post_id=post.id, views=100, likes=5, replies=1, reposts=0, quotes=0, captured_at=now)
    db.add(metric)
    await db.commit()

    monkeypatch.setattr(settings, "analytics_cache_seconds", 300)
    first = (await client.get("/api/threads/analytics")).json()
    assert sum(point["impressions"] for point in first["performance"]) == 100

    # The data changes under the cache; within the TTL the response is frozen.
    metric.views = 555
    await db.commit()
    second = (await client.get("/api/threads/analytics")).json()
    assert sum(point["impressions"] for point in second["performance"]) == 100

    # TTL 0 (the test default) always recomputes.
    monkeypatch.setattr(settings, "analytics_cache_seconds", 0)
    third = (await client.get("/api/threads/analytics")).json()
    assert sum(point["impressions"] for point in third["performance"]) == 555

