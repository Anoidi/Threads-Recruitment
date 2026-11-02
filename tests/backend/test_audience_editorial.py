from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.audience_editorial import select_audience_questions
from app.audience_profile import audience_profile
from app.content_evidence import _response_rate, content_evidence, evidence_brief
from app.models import CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost


def question(i, message, *, answered=False, ignored=False):
    return SimpleNamespace(id=i, message=message, classification=None, answered=answered, ignored=ignored)


def test_use_real_unanswered_questions_even_when_recent_comments_are_noise():
    rows = [question(i, "Дякую, чудово!") for i in range(1, 7)] + [
        question(7, "Яка ставка?", answered=True),
        question(8, "Чи є житло?"),
        question(9, "Скільки коштує житло?"),
        question(10, "Яка ставка?", ignored=True),
    ]
    selected = select_audience_questions(rows)
    assert [row[0] for row in selected] == [8, 9, 7]


def test_question_context_masks_contacts_and_deduplicates():
    rows = [question(1, "Чи є житло? @alice alice@example.com +48 123 456 789"),
            question(2, "Чи є житло? @bob bob@example.com +48 987 654 321")]
    result = select_audience_questions(rows)
    assert len(result) == 1
    text = result[0][1]
    assert "житло" in text
    assert "alice" not in text and "123" not in text and "@" not in text


@pytest.mark.parametrize("age,views,likes,expected", [
    (2, 200, 10, 5.0), (0.2, 200, 10, None), (9, 200, 10, None),
    (2, 0, 10, None), (2, 200, None, None), (2, 200, -1, None),
])
def test_reactions_do_not_compare_unmeasured_or_different_age_posts(age, views, likes, expected):
    now = datetime.now(timezone.utc)
    post = SimpleNamespace(published_at=now - timedelta(days=age))
    metric = SimpleNamespace(captured_at=now, views=views, likes=likes, replies=0, reposts=0, quotes=0)
    assert _response_rate(post, metric) == expected


@pytest.mark.asyncio
async def test_ignored_interest_does_not_teach_the_writer_a_false_success(db):
    now = datetime.now(timezone.utc)
    post = ThreadsPost(content="Що уточнити про житло", content_type="FAQ", language="uk",
                       status="PUBLISHED", remote_id="editorial-live-post", content_subject="HOUSING",
                       published_at=now - timedelta(days=2))
    db.add(post)
    await db.flush()
    for i, ignored in enumerate([True, False]):
        item = ThreadsInteraction(remote_id=f"editorial-reply-{i}", kind="REPLY", username=f"reader{i}",
                                  message="Чи є житло?", post_id=post.id)
        db.add(item)
        await db.flush()
        db.add(CandidateSignal(interaction_id=item.id, username=item.username, intent="HOUSING_QUESTION",
                               confidence=0.9, status="IGNORED" if ignored else "NEW"))
    db.add(PostMetric(post_id=post.id, views=200, likes=10, replies=2, reposts=0, quotes=0, captured_at=now))
    await db.commit()
    report = await content_evidence(db)
    row = report["by_theme"][0]
    assert row["signals"] == 1
    assert row["median_response_rate"] == 6.0
    assert row["response_sample"] == 1
    assert row["response_evidence"] == "exploratory"
    assert "24ч–7д" in evidence_brief(report)


@pytest.mark.asyncio
async def test_audience_demand_counts_question_once_and_ignores_rejected_signals(db):
    for i, ignored in enumerate([True, False]):
        item = ThreadsInteraction(remote_id=f"demand-{i}", kind="REPLY", username=f"reader{i}",
                                  message="Яка ставка?", classification="SALARY_QUESTION", ignored=ignored)
        db.add(item)
        await db.flush()
        db.add(CandidateSignal(interaction_id=item.id, username=item.username, intent="SALARY_QUESTION",
                               confidence=0.9, status="IGNORED" if ignored else "NEW"))
    await db.commit()
    profile = await audience_profile(db)
    pay = next(row for row in profile["demands"] if row["theme"] == "PAY")
    assert pay["asked"] == 1
    assert profile["sample"]["questions"] == 1
    assert profile["sample"]["signals"] == 1
