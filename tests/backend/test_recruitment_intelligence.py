"""Regression tests for the recruitment intelligence and the result ranking.

A recruitment account that optimises reach optimises the wrong thing. A post
with a thousand views and no candidate is worth less than a post with two
applicants, and the ranking has to say so.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.content_evidence import content_evidence, evidence_brief
from app.models import (
    Candidate, CandidateSignal, PostMetric, ThreadsInteraction, ThreadsPost, Vacancy,
)
from app.recruitment_intelligence import demand_brief, vacancy_demand


def _vacancy(title: str = "Вакансия") -> Vacancy:
    return Vacancy(
        title=title, description="Описание.", location="Kraków", salary="30 zł/год",
        currency="PLN", schedule="2 зміни", language="uk", active=True,
    )


def _published(content: str, vacancy_id: int | None = None, views: int = 100) -> ThreadsPost:
    post = ThreadsPost(
        content=content, content_type="EDUCATIONAL", language="uk",
        status="PUBLISHED", remote_id=f"remote-{content[:14]}", vacancy_id=vacancy_id,
        published_at=datetime.now(timezone.utc) - timedelta(days=2),
        content_subject="PAY" if "ставк" in content.lower() else "HOUSING",
    )
    return post


async def _add_metric(db, post, views: int, replies: int = 0):
    db.add(PostMetric(
        post_id=post.id, views=views, likes=0, replies=replies, reposts=0, quotes=0,
        captured_at=datetime.now(timezone.utc),
    ))


@pytest.mark.asyncio
async def test_vacancies_without_interest_are_reported_as_a_deficit(db):
    popular = _vacancy("Популярная")
    quiet = _vacancy("Тихая")
    db.add_all([popular, quiet])
    await db.flush()

    popular_post = _published("Ставка 30 zл/год для всіх", vacancy_id=popular.id)
    db.add(popular_post)
    await db.flush()
    interaction = ThreadsInteraction(
        remote_id="i-1", kind="REPLY", username="candidate", message="Какая ставка?",
        classification="SALARY_QUESTION", post_id=popular_post.id,
    )
    db.add(interaction)
    await db.flush()
    db.add(CandidateSignal(interaction_id=interaction.id, username="candidate", intent="SALARY_QUESTION", confidence=0.9))
    await db.commit()

    demand = await vacancy_demand(db)

    deficit_ids = {row["vacancy_id"] for row in demand["deficits"]}
    assert quiet.id in deficit_ids
    assert popular.id not in deficit_ids
    assert demand["top"]["vacancy_id"] == popular.id
    assert "без единого обращения" in demand["summary"]
    assert demand_brief(demand)


@pytest.mark.asyncio
async def test_candidates_outrank_reach_in_the_result_ranking(db):
    """A post with two candidates beats a post with ten times the views."""
    loud = _published("Житло 650 зл на місяць у Кракові")
    quiet = _published("Ставка 30 зл/год netto для студентів")
    db.add_all([loud, quiet])
    await db.flush()
    await _add_metric(db, loud, views=30000)
    await _add_metric(db, quiet, views=4000)

    interaction = ThreadsInteraction(
        remote_id="i-2", kind="REPLY", username="candidate", message="Какая ставка?",
        classification="SALARY_QUESTION", post_id=quiet.id,
    )
    db.add(interaction)
    await db.flush()
    signal = CandidateSignal(interaction_id=interaction.id, username="candidate", intent="SALARY_QUESTION", confidence=0.9)
    db.add(signal)
    await db.flush()
    db.add(Candidate(source_signal_id=signal.id, name="candidate", threads_username="candidate"))
    await db.commit()

    evidence = await content_evidence(db)

    themes = {row["name"]: row for row in evidence["by_theme"]}
    assert themes["PAY"]["candidates"] == 1
    assert themes["PAY"]["signals"] == 1
    assert themes["HOUSING"]["candidates"] == 0
    # The theme that produced a candidate comes first, even with far less reach.
    assert evidence["by_theme"][0]["name"] == "PAY"
    assert evidence["best_theme"]["name"] == "PAY"
    assert "кандидат" in evidence["recommendation"]
    brief = evidence_brief(evidence)
    assert "сигналов кандидатов" in brief


@pytest.mark.asyncio
async def test_a_post_without_metrics_is_not_treated_as_zero_reach(db):
    post = _published("Житло 650 зл")
    db.add(post)
    await db.commit()

    evidence = await content_evidence(db)

    row = evidence["by_theme"][0]
    assert row["measured"] == 0
    assert row["median_views"] is None
