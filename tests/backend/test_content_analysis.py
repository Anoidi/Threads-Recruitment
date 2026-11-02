from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Item
import pytest
from app.content_analysis import analyze_content, editorial_evidence
from app.enums import PostStatus
from app.models import ThreadsPost, PostMetric, PostApproval

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def post(id=1, kind='FAQ', **changes):
    values = dict(id=id, content_type=kind, language='uk', status=PostStatus.PUBLISHED, remote_id=f'real-{id}', published_at=NOW-timedelta(hours=30))
    return Item(**(values | changes))


def metric(post_id=1, **changes):
    values = dict(id=post_id, post_id=post_id, captured_at=NOW, views=100, likes=5, replies=2, reposts=1, quotes=0)
    return Item(**(values | changes))


def test_missing_zero_young_and_mock_are_not_comparable():
    posts = [post(i) for i in range(1, 6)]
    posts[3].published_at = NOW-timedelta(hours=2)
    posts[4].remote_id = 'mock-5'
    report = analyze_content(posts, [metric(2, likes=None), metric(3, views=0), metric(4), metric(5)])
    assert report['published_posts'] == 4
    assert report['posts_with_metrics'] == 3
    assert report['comparable_posts'] == 0
    assert report['insufficient_data']


def test_latest_snapshot_once_and_age_at_capture_not_today():
    report = analyze_content([post()], [metric(1, captured_at=NOW-timedelta(hours=5), views=50), metric(1, id=2)])
    assert report['posts_with_metrics'] == 1
    assert report['cohorts'][0]['views'] == 100
    assert report['cohorts'][0]['age_bucket'] == '24–48h'


def test_no_winner_across_languages_or_age_windows():
    posts = [post(i, 'FAQ' if i <= 3 else 'QUESTION', language='uk' if i <= 3 else 'pl') for i in range(1, 7)]
    assert not analyze_content(posts, [metric(i) for i in range(1, 7)])['hypotheses']
    for p in posts:
        p.language = 'uk'
        if p.id > 3:
            p.published_at = NOW-timedelta(days=10)
    assert not analyze_content(posts, [metric(i) for i in range(1, 7)])['hypotheses']


def test_supported_comparison_is_explicitly_a_hypothesis():
    posts = [post(i, 'FAQ' if i <= 3 else 'QUESTION') for i in range(1, 7)]
    report = analyze_content(posts, [metric(i, likes=10 if i <= 3 else 1) for i in range(1, 7)])
    assert len(report['hypotheses']) == 1
    assert 'FAQ' in report['hypotheses'][0] and 'Гипотеза' in report['hypotheses'][0]


@pytest.mark.asyncio
async def test_evidence_does_not_edit_existing_posts_and_ignores_orphan_metrics(db):
    p = ThreadsPost(content='Старый текст не меняется', content_type='FAQ', language='uk', status=PostStatus.PUBLISHED, remote_id='real', published_at=NOW-timedelta(hours=30))
    draft = ThreadsPost(content='Черновик', content_type='FAQ', language='uk')
    db.add_all([p, draft])
    await db.flush()
    db.add(PostMetric(post_id=draft.id, views=100, captured_at=NOW))
    await db.flush()
    result = await editorial_evidence(db)
    assert result['posts_with_metrics'] == 0
    await db.refresh(p)
    assert p.content == 'Старый текст не меняется'
    assert len(result['recent_topics']) == 2


@pytest.mark.asyncio
async def test_planner_receives_read_only_evidence(db, monkeypatch):
    from app.ai import AIService
    from worker.main import content_plan
    seen = {}
    async def plan(self, **kwargs):
        seen.update(kwargs)
        return []
    monkeypatch.setattr(AIService, 'plan_content', plan)
    await content_plan(db, {'count': 1, 'language': 'uk'})
    assert seen['editorial_analysis']['posts_with_metrics'] == 0
    assert seen['editorial_analysis']['insufficient_data']


@pytest.mark.asyncio
async def test_feedback_uses_latest_human_decision_not_automatic_approval(db):
    p = ThreadsPost(content='Существующий текст', content_type='FAQ', language='uk')
    db.add(p)
    await db.flush()
    db.add_all([
        PostApproval(post_id=p.id, post_version=1, actor='local-operator', decision='REJECTED', note='Слишком шаблонно', created_at=NOW-timedelta(hours=1)),
        PostApproval(post_id=p.id, post_version=1, actor='local-operator', decision='APPROVED', note='Короче. Телефон +48 577 017 187, @handle', created_at=NOW),
        PostApproval(post_id=p.id, post_version=2, actor='automation-policy', decision='APPROVED', note='Auto approved'),
    ])
    await db.flush()
    evidence = await editorial_evidence(db)
    assert len(evidence['operator_feedback']) == 1
    feedback = evidence['operator_feedback'][0]
    assert feedback['decision'] == 'APPROVED'
    assert '577' not in feedback['note'] and '@handle' not in feedback['note']
    assert evidence['learning_mode'] == 'editorial_feedback_not_model_training'
    await db.refresh(p)
    assert p.content == 'Существующий текст'


@pytest.mark.asyncio
async def test_repeated_preferences_require_distinct_review_versions(db):
    post = ThreadsPost(content='Исторический текст', content_type='FAQ', language='uk')
    db.add(post)
    await db.flush()
    db.add_all([
        PostApproval(post_id=post.id, post_version=1, actor='local-operator', decision='REJECTED', note='Слишком шаблонно'),
        PostApproval(post_id=post.id, post_version=1, actor='local-operator', decision='REJECTED', note='Слишком шаблонно'),
    ])
    await db.flush()
    assert (await editorial_evidence(db))['repeated_editorial_preferences'] == []
    db.add(PostApproval(post_id=post.id, post_version=2, actor='local-operator', decision='REJECTED', note='слишком шаблонно'))
    await db.flush()
    patterns = (await editorial_evidence(db))['repeated_editorial_preferences']
    assert len(patterns) == 1
    assert patterns[0]['occurrences'] == 2
    assert patterns[0]['decision'] == 'REJECTED'
    await db.refresh(post)
    assert post.content == 'Исторический текст'
