from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ai import _compact_evidence
from app.growth_learning import growth_report, opening_style, study_posts
from app.models import Candidate, CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def post(i, *, language="uk", kind="FAQ", content="Житло: що уточнити?", age=72, **extra):
    return SimpleNamespace(id=i, language=language, content_type=kind, content=content,
                           published_at=NOW - timedelta(hours=age), status="PUBLISHED",
                           remote_id=extra.get("remote_id", f"live-{i}"), metrics_status=extra.get("metrics_status", "OK"))


def snapshot(p, *, hours=24, views=100, replies=10, **extra):
    values = dict(id=p.id * 100 + hours, post_id=p.id, captured_at=p.published_at + timedelta(hours=hours),
                  views=views, likes=2, replies=replies, reposts=0, quotes=0)
    values.update(extra)
    return SimpleNamespace(**values)


def test_same_day_comparison_keeps_24h_snapshot_when_lifetime_total_changes():
    p = post(1)
    result = study_posts([p], [snapshot(p), snapshot(p, hours=25, views=200),
                               snapshot(p, hours=72, views=10000, replies=0)], now=NOW)
    row = next(r for r in result["groups"] if r["dimension"] == "format")
    assert row["median_views"] == 100
    assert row["median_reply_rate"] == 100
    assert result["coverage"]["comparable"] == 1


def test_question_in_the_body_is_not_a_question_hook():
    assert opening_style("Перевірте умови договору. Яка у вас ставка?")[0] == "statement"
    assert opening_style("Скільки коштує житло? Пояснюємо умови.")[0] == "question"


def test_sparse_conversation_suggests_exploration_not_a_winner():
    posts = [post(i, kind="FAQ" if i < 4 else "EDUCATIONAL" if i < 7 else "RECRUITER_STORY") for i in range(1, 8)]
    report = study_posts(posts, [snapshot(p, replies=2 if p.id == 7 else 0) for p in posts], now=NOW)
    test = next(t for t in report["next_tests"] if t["dimension"] == "format")
    assert test["status"] == "explore"
    assert test["preferred"] == "RECRUITER_STORY"
    assert "не победитель" in test["reason"]


def test_missing_partial_future_and_old_metrics_do_not_become_failures():
    posts = [post(1), post(2), post(3), post(4), post(5, age=1)]
    metrics = [snapshot(posts[0], hours=1), snapshot(posts[1], hours=48),
               snapshot(posts[2], likes=None), snapshot(posts[3], replies=-1), snapshot(posts[4])]
    result = study_posts(posts, metrics, now=NOW)
    assert result["coverage"]["comparable"] == 0
    assert result["coverage"]["pending"] == 1
    assert result["coverage"]["missing_window"] == 4
    assert not result["groups"]
    assert "сбор insights" in result["recommendation"]


def test_measured_zero_is_retained_without_infinite_engagement_or_winner():
    p = post(1)
    report = study_posts([p], [snapshot(p, views=0, replies=0)], now=NOW)
    assert report["coverage"]["zero_views"] == 1
    assert report["coverage"]["missing_window"] == 0
    assert report["groups"][0]["median_reply_rate"] is None
    assert all(t["status"] == "collect" for t in report["next_tests"])


def test_mock_unavailable_and_outside_window_do_not_teach_planner():
    posts = [post(1, remote_id="mock-test"), post(2, age=40 * 24),
             post(3, metrics_status="UNAVAILABLE")]
    report = study_posts(posts, [snapshot(p) for p in posts], now=NOW)
    assert report["coverage"]["published"] == 1
    assert report["coverage"]["unavailable"] == 1
    assert not report["groups"]


def test_high_views_do_not_beat_conversation_and_languages_are_separate():
    posts = [post(i, kind="FAQ" if i < 4 else "EDUCATIONAL") for i in range(1, 7)]
    metrics = [snapshot(p, views=100 if p.id < 4 else 10000, replies=5 if p.id < 4 else 1) for p in posts]
    report = study_posts(posts, metrics, now=NOW)
    test = next(t for t in report["next_tests"] if t["dimension"] == "format")
    assert test["status"] == "test"
    assert test["preferred"] == "FAQ"
    assert test["comparison"] == "EDUCATIONAL"
    for p in posts[3:]:
        p.language = "pl"
    report = study_posts(posts, metrics, now=NOW)
    assert not any(t["status"] == "test" for t in report["next_tests"])


def test_small_samples_and_silent_posts_do_not_produce_a_winner():
    posts = [post(i, kind="FAQ" if i < 4 else "QUESTION") for i in range(1, 7)]
    metrics = [snapshot(p, replies=0) for p in posts]
    report = study_posts(posts, metrics, now=NOW)
    assert next(t for t in report["next_tests"] if t["dimension"] == "format")["status"] == "inconclusive"
    report = study_posts(posts[:4], metrics, now=NOW)
    assert not any(t["preferred"] for t in report["next_tests"])


def test_prompt_uses_only_requested_language_tests():
    posts = [post(i, language="pl" if i < 4 else "uk") for i in range(1, 7)]
    report = study_posts(posts, [snapshot(p) for p in posts], now=NOW)
    for t in report["next_tests"]:
        t["reason"] = "POLISH_TEST" if t["language"] == "pl" else "UKRAINIAN_TEST"
    compact = _compact_evidence({"growth_learning": report}, language="uk")
    assert "UKRAINIAN_TEST" in compact["next_performance_test"]
    assert "POLISH_TEST" not in compact["next_performance_test"]
    assert "engagement bait" in compact["next_performance_test"]


@pytest.mark.asyncio
async def test_report_joins_real_recruitment_outcomes_and_ignores_rejected_interest(db, client):
    published = datetime.now(timezone.utc) - timedelta(days=3)
    p = ThreadsPost(content="Чи є житло?", content_type="FAQ", language="uk", status="PUBLISHED",
                    remote_id="growth-live-post", published_at=published)
    db.add(p)
    await db.flush()
    db.add(PostMetric(post_id=p.id, captured_at=published + timedelta(hours=24),
                      views=100, replies=3, likes=2, reposts=0, quotes=0))
    for i, status in enumerate(["NEW", "IGNORED", "NEW"]):
        interaction = ThreadsInteraction(post_id=p.id, remote_id=f"growth-comment-{i}", username=f"reader{i}",
                                         kind="REPLY", message="Цікавить вакансія", ignored=i == 2)
        db.add(interaction)
        await db.flush()
        signal = CandidateSignal(interaction_id=interaction.id, username=interaction.username,
                                 intent="JOB_INTEREST", confidence=0.9, status=status)
        db.add(signal)
        await db.flush()
        if i == 0:
            db.add(Candidate(source_signal_id=signal.id, name="Test candidate", contact_value="test@example.test",
                             confirmed_at=datetime.now(timezone.utc), consent_given=True))
    await db.commit()
    report = await growth_report(db)
    row = next(r for r in report["groups"] if r["dimension"] == "format")
    assert row["signals"] == 1
    assert row["candidates"] == 1
    response = await client.get("/api/threads/analytics")
    assert response.status_code == 200
    assert response.json()["growth_learning"]["coverage"]["comparable"] == 1


@pytest.mark.asyncio
async def test_editorial_evidence_carries_learning_to_daily_planner(db):
    from app.content_analysis import editorial_evidence
    evidence = await editorial_evidence(db)
    assert "growth_learning" in evidence
    assert "сбор insights" in _compact_evidence(evidence, language="uk")["next_performance_test"]
