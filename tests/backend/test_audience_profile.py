"""Regression tests for the audience profile.

The profile is what turns the captured replies into a content decision: what
the audience asks, what the account already answered, and where the gap is.
Two mistakes would make the planner follow the wrong signal, so both are
pinned down here: a comment is not a question, and the subject of a post is
stored rather than re-derived from its wording.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.audience_profile import audience_brief, audience_profile
from app.content_subjects import detect_subjects, primary_subject, theme_of
from app.enums import MetricsStatus, PostStatus
from app.models import CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost


def _post(content: str, *, subject: str | None = None, views: int = 100, published_days_ago: int = 3) -> ThreadsPost:
    published = datetime.now(timezone.utc) - timedelta(days=published_days_ago)
    return ThreadsPost(
        content=content, content_type="FAQ", language="uk", status=PostStatus.PUBLISHED,
        remote_id=f"remote-{content[:12]}", published_at=published,
        content_subject=subject, metrics_status=MetricsStatus.OK,
    )


def _interaction(message: str, classification: str | None, days_ago: int = 2) -> ThreadsInteraction:
    return ThreadsInteraction(
        remote_id=f"r-{message[:16]}-{days_ago}", kind="REPLY", username="candidate",
        message=message, classification=classification,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def test_a_comment_is_not_a_question():
    from app.audience_profile import is_question

    assert is_question("Чи є житло і яка ставка?", "HOUSING_QUESTION")
    assert is_question("А яка зарплата?", None)
    assert not is_question("Цікавить", "OTHER")
    assert not is_question("Скарга на менеджера", "COMPLAINT")


def test_theme_detection_reads_the_condition_not_the_wording():
    first = "У вакансіях житло вираховується з зарплати, 600–650 зл/міс."
    second = "Житло 650 зл на місяць тримають з зарплати."
    assert primary_subject(first) == primary_subject(second) == "HOUSING"
    assert "PAY" in detect_subjects("Ставка 30 зл/год для студентів")


def test_theme_of_uses_the_stored_classification_as_backup():
    # The keyword classifier marks most real comments as OTHER.
    assert theme_of("Що там з житлом?", "HOUSING_QUESTION") == "HOUSING"
    assert theme_of("Коли виплата?", "SALARY_QUESTION") == "PAY"


@pytest.mark.asyncio
async def test_profile_compares_demand_with_coverage(db):
    db.add_all([
        _interaction("Чи є житло і яка ставка?", "HOUSING_QUESTION"),
        _interaction("Яка зарплата і як виплачують?", "SALARY_QUESTION"),
        _interaction("Яка зарплата?", "SALARY_QUESTION"),
        _interaction("Цікавить", "OTHER"),
        _post("Графік: 2 зміни, 8 годин.", subject="SCHEDULE"),
    ])
    await db.commit()

    profile = await audience_profile(db, days=30)

    pay = next(item for item in profile["demands"] if item["theme"] == "PAY")
    assert pay["asked"] == 2
    # The plain comment is engagement, not demand.
    assert profile["sample"]["other_comments"] == 1
    assert profile["sample"]["questions"] == 3
    # Schedule was published, pay was not: pay is the gap.
    gaps = {item["theme"]: item for item in profile["gaps"]}
    assert "PAY" in gaps
    assert gaps["PAY"]["published"] == 0
    assert "ставку" in profile["recommendation"]


@pytest.mark.asyncio
async def test_coverage_uses_the_stored_subject_not_the_wording(db):
    # A contract post that mentions pay is a contract post.
    post = _post("Договір: umowa zlecenie. Уточніть, як нараховуються суми.", subject="CONTRACT")
    db.add(post)
    await db.commit()

    profile = await audience_profile(db, days=30)

    coverage = {item["theme"]: item for item in profile["coverage"]}
    assert "CONTRACT" in coverage
    assert "PAY" not in coverage


@pytest.mark.asyncio
async def test_signals_count_as_demand(db):
    db.add(CandidateSignal(interaction_id=1, username="candidate", intent="SALARY_QUESTION", confidence=0.9))
    await db.commit()

    profile = await audience_profile(db, days=30)

    pay = next(item for item in profile["demands"] if item["theme"] == "PAY")
    assert pay["from_signals"] == 1
    assert pay["asked"] == 1


@pytest.mark.asyncio
async def test_profile_reports_an_empty_window_honestly(db):
    profile = await audience_profile(db, days=30)

    assert profile["demands"] == []
    assert profile["gaps"] == []
    assert "не собраны" in profile["recommendation"] or "нет" in profile["recommendation"].lower()
    assert profile["limitations"]


@pytest.mark.asyncio
async def test_brief_is_compact_and_names_the_gap(db):
    db.add_all([
        _interaction("Яка зарплата?", "SALARY_QUESTION"),
        _interaction("Яка зарплата?", "SALARY_QUESTION", days_ago=3),
        _post("Житло: 650 zł/міс.", subject="HOUSING"),
    ])
    await db.commit()

    profile = await audience_profile(db, days=30)
    brief = audience_brief(profile)

    assert "ставку" in brief
    assert len(brief) < 1200
