"""Format experiments: one confirmed fact, several formats, one honest report.

The rules these tests pin down:

* the fact comes from the vacancy source and is never invented;
* every post of a run carries that same fact, so the formats are comparable;
* a post that does not really belong to its variant is excluded from the
  comparison instead of counting as a losing variant;
* the report says "not enough data" until there is more than one post per
  variant, and it always shows the sample sizes.
"""
import pytest
from sqlalchemy import select

from app.enums import PostStatus
from app.experiments import (
    FORMATS, detect_format, experiment_report, fact_present, render_format,
    start_experiment, verify_membership,
)
from app.models import ThreadExperiment, ThreadExperimentAssignment, ThreadsPost
from worker.main import content_plan


async def _vacancy(client, **overrides):
    payload = {
        "title": "Magazynier",
        "description": "Praca przy pakowaniu zamówień",
        "location": "Kraków",
        "salary": "30 PLN/hour",
        "schedule": "2 zmiany",
        "language": "pl",
    }
    payload.update(overrides)
    response = await client.post("/api/vacancies", json=payload)
    assert response.status_code == 201
    return response.json()


def _plan_in_format(instruction: str, fact: str) -> dict:
    """Build one post in the format the planner was asked for."""
    if "QUESTION" in instruction:
        content = f"Ставка: {fact}. Уточніть деталі у роботодавця. Що ще варто уточнити перед виїздом?"
    elif "TIPS" in instruction:
        content = (
            f"1. Ставка: {fact}.\n"
            "2. Уточніть деталі у роботодавця перед виїздом.\n"
            "3. Напишіть у приватні повідомлення."
        )
    else:
        content = f"Ставка: {fact}. Уточніть деталі у роботодавця перед виїздом."
    return {"content": content, "hook": content.split("\n", 1)[0], "content_type": "FAQ"}


@pytest.mark.asyncio
async def test_the_fact_comes_from_the_vacancy_and_is_never_invented(db, client):
    vacancy = await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()
    assert experiment.fact == vacancy["salary"]
    assert experiment.subject == "PAY"
    assert [item["key"] for item in experiment.variants] == list(FORMATS)


@pytest.mark.asyncio
async def test_every_post_of_a_run_carries_the_same_fact(db, client, monkeypatch):
    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(ThreadExperimentAssignment.experiment_id == experiment.id)
    )).scalars())
    assert len(assignments) == len(FORMATS)
    for assignment in assignments:
        post = await db.get(ThreadsPost, assignment.post_id)
        assert fact_present(post.content, experiment.fact), post.content
    # The formats really differ, otherwise nothing is being compared.
    formats = {assignment.format for assignment in assignments}
    assert formats == set(FORMATS)
    for assignment in assignments:
        post = await db.get(ThreadsPost, assignment.post_id)
        assert detect_format(post.content) == assignment.format


@pytest.mark.asyncio
async def test_a_post_in_the_wrong_format_is_excluded_from_the_comparison(db, client, monkeypatch):
    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        # The model ignores the format and always writes a question.
        return [{
            "content": f"Ставка: {kwargs.get('fact_text') or ''}. Що ще варто уточнити?",
            "hook": "Ставка", "content_type": "FAQ",
        }]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(ThreadExperimentAssignment.experiment_id == experiment.id)
    )).scalars())
    question = next(item for item in assignments if item.format == "QUESTION")
    fact = next(item for item in assignments if item.format == "FACT")
    assert question.verified is True
    # The FACT variant got a question, so it does not belong to that variant.
    assert fact.verified is False
    assert fact.mismatch_reason == "format"

    report = await experiment_report(db, experiment)
    rows = {row["key"]: row for row in report["variants"]}
    assert rows["FACT"]["excluded"] == 1
    assert rows["FACT"]["published"] == 0
    assert rows["QUESTION"]["excluded"] == 0
    assert len(report["excluded"]) == 2


@pytest.mark.asyncio
async def test_the_report_says_that_one_post_per_variant_is_not_enough(db, client, monkeypatch):
    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    report = await experiment_report(db, experiment)
    # The drafts exist, but nothing is published yet: there is nothing to
    # compare, and the report says exactly that.
    assert report["verdict"] == "NO_PUBLISHED"
    assert report["winner"] is None
    for row in report["variants"]:
        assert row["published"] == 0
        assert row["assigned"] == 1
        assert row["verified"] == 1


@pytest.mark.asyncio
async def test_a_published_post_per_variant_still_needs_more_than_one(db, client, monkeypatch):
    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    for post in (await db.execute(select(ThreadsPost))).scalars():
        post.status = PostStatus.PUBLISHED
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["verdict"] == "INSUFFICIENT_DATA"
    for row in report["variants"]:
        assert row["published"] == 1


@pytest.mark.asyncio
async def test_a_wide_publication_window_is_reported_as_a_caveat(db, client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(ThreadExperimentAssignment.experiment_id == experiment.id)
    )).scalars())
    base = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
    for index, assignment in enumerate(assignments):
        post = await db.get(ThreadsPost, assignment.post_id)
        post.status = PostStatus.PUBLISHED
        # The first variant appears three days before the others.
        post.published_at = base + timedelta(days=3 * index)
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["window"]["span_hours"] == 144.0
    assert any("разброс" in item for item in report["caveats"])
    assert "Разброс публикаций 144 ч." in report["verdict_text"] or report["verdict"] != "RESULT"


@pytest.mark.asyncio
async def test_the_experiment_stops_after_its_rounds(db, client, monkeypatch):
    from app.ai import AIService
    from app.experiments import EXPERIMENT_ROUNDS, experiment_needs_round

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})
    # A round that has not reached the audience is not a finished round: the
    # experimenter must not borrow the feed again while round one is still
    # queued, or the account floods itself with the same fact.
    assert await experiment_needs_round(db, experiment) is False

    for post in (await db.execute(select(ThreadsPost))).scalars():
        from datetime import datetime, timezone

        post.status = PostStatus.PUBLISHED
        post.published_at = datetime.now(timezone.utc)
    await db.commit()
    assert await experiment_needs_round(db, experiment) is True

    await content_plan(db, {"count": 3, "language": "uk"})
    assert await experiment_needs_round(db, experiment) is False

    assignments = list((await db.execute(select(ThreadExperimentAssignment))).scalars())
    assert len(assignments) == len(FORMATS) * EXPERIMENT_ROUNDS

    # The account goes back to its normal content: no new experiment posts.
    await content_plan(db, {"count": 1, "language": "uk"})
    after = list((await db.execute(select(ThreadExperimentAssignment))).scalars())
    assert len(after) == len(assignments)


@pytest.mark.asyncio
async def test_the_experimenter_agent_opens_an_experiment_and_reports_it(db, client, monkeypatch):
    from app.ai import AIService
    from app.enums import AgentRole
    from app.models import AgentRun
    from worker.main import run_agent

    await _vacancy(client)

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    run = AgentRun(role=AgentRole.EXPERIMENTER, status="QUEUED")
    db.add(run)
    await db.flush()
    await run_agent(db, run.id, AgentRole.EXPERIMENTER)
    await db.refresh(run)

    assert run.status == "COMPLETED"
    assert "Эксперимент" in (run.result_summary or "")
    experiments = list((await db.execute(select(ThreadExperiment))).scalars())
    assert len(experiments) == 1
    assert experiments[0].status == "RUNNING"
    assignments = list((await db.execute(select(ThreadExperimentAssignment))).scalars())
    assert len(assignments) == len(FORMATS)


@pytest.mark.asyncio
async def test_the_model_failure_falls_back_to_the_same_confirmed_fact(db, client, monkeypatch):
    from fastapi import HTTPException

    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        raise HTTPException(502, "model unavailable")

    monkeypatch.setattr(AIService, "plan_content", plan)
    summary = await content_plan(db, {"count": 3, "language": "uk"})

    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(ThreadExperimentAssignment.experiment_id == experiment.id)
    )).scalars())
    assert len(assignments) == len(FORMATS)
    for assignment in assignments:
        assert assignment.verified is True
        post = await db.get(ThreadsPost, assignment.post_id)
        assert fact_present(post.content, experiment.fact)
        assert detect_format(post.content) == assignment.format
    assert "Резервный" in summary


@pytest.mark.asyncio
async def test_an_experiment_reports_candidates_first_and_not_views(db, client, monkeypatch):
    from app.ai import AIService
    from app.models import Candidate, CandidateSignal, ThreadsInteraction

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})
    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(ThreadExperimentAssignment.experiment_id == experiment.id)
    )).scalars())
    # Two posts per variant, each with a closed 24-hour observation window.
    from datetime import datetime, timedelta, timezone
    from app.models import PostMetric

    base = datetime.now(timezone.utc) - timedelta(hours=30)
    for assignment in assignments:
        post = await db.get(ThreadsPost, assignment.post_id)
        post.status = PostStatus.PUBLISHED
        post.published_at = base
        clone = ThreadsPost(
            vacancy_id=post.vacancy_id, content=post.content + " ", hook=post.hook,
            content_type=post.content_type, language=post.language, status=PostStatus.PUBLISHED,
            published_at=base,
        )
        db.add(clone)
        await db.flush()
        for target in (post, clone):
            db.add(PostMetric(
                post_id=target.id, views=100, likes=0, replies=0, reposts=0, quotes=0,
                captured_at=base + timedelta(hours=24, minutes=30),
            ))
        db.add(ThreadExperimentAssignment(
            experiment_id=experiment.id, variant=assignment.variant, format=assignment.format,
            post_id=clone.id, fact=experiment.fact, verified=True,
        ))
    # Two candidates only for the QUESTION variant.
    question = next(item for item in assignments if item.format == "QUESTION")
    post = await db.get(ThreadsPost, question.post_id)
    for index in (1, 2):
        interaction = ThreadsInteraction(
            post_id=post.id, remote_id=f"r-{index}", kind="REPLY", username=f"u{index}",
            message="m", occurred_at=base + timedelta(hours=2),
        )
        db.add(interaction)
        await db.flush()
        signal = CandidateSignal(interaction_id=interaction.id, vacancy_id=post.vacancy_id, username=f"u{index}", intent="i", confidence=0.9)
        db.add(signal)
        await db.flush()
        candidate = Candidate(name=f"C{index}", source_signal_id=signal.id, threads_username=f"u{index}")
        db.add(candidate)
        await db.flush()
        signal.candidate_id = candidate.id
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["verdict"] == "RESULT"
    assert report["primary_window"] == 24
    assert report["winner"]["key"] == "QUESTION"
    assert report["winner"]["candidates"] == 2
    rows = {row["key"]: row for row in report["variants"]}
    assert rows["QUESTION"]["published"] == 2
    assert rows["FACT"]["published"] == 2
    assert rows["FACT"]["candidates"] == 0


@pytest.mark.asyncio
async def test_the_experiment_endpoint_exposes_the_report(db, client):
    await _vacancy(client)
    response = await client.post("/api/threads/experiments", json={"subject": "PAY"})
    assert response.status_code == 201
    body = response.json()
    assert body["subject"] == "PAY"
    assert body["fact"]
    assert len(body["variants"]) == len(FORMATS)

    listing = await client.get("/api/threads/experiments")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["current"]["experiment_id"] == body["experiment_id"]
    assert payload["experiments"]


@pytest.mark.asyncio
async def test_concluding_freezes_the_report(db, client):
    await _vacancy(client)
    created = await client.post("/api/threads/experiments", json={"subject": "PAY"})
    experiment_id = created.json()["experiment_id"]
    response = await client.post(f"/api/threads/experiments/{experiment_id}/conclude")
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "CONCLUDED"
    assert report["verdict"]
    row = await db.get(ThreadExperiment, experiment_id)
    assert row.status == "CONCLUDED"
    assert row.report


@pytest.mark.asyncio
async def test_the_experiment_is_part_of_the_editorial_evidence(db, client, monkeypatch):
    from app.ai import AIService

    await _vacancy(client)
    experiment = await start_experiment(db, subject="PAY")
    await db.commit()

    async def plan(self, **kwargs):
        return [_plan_in_format(kwargs.get("format_instruction") or "", kwargs.get("fact_text") or "")]

    monkeypatch.setattr(AIService, "plan_content", plan)
    await content_plan(db, {"count": 3, "language": "uk"})

    from app.content_analysis import editorial_evidence

    evidence = await editorial_evidence(db)
    assert evidence["experiment"] is not None
    assert evidence["experiment"]["experiment_id"] == experiment.id
    assert evidence["experiment"]["verdict"] == "NO_PUBLISHED"


def test_the_membership_check_rejects_a_post_that_lost_the_fact():
    verified, reason = verify_membership("Ставка: 30 PLN/hour. Уточніть деталі.", "FACT", "30 PLN/hour")
    assert verified is True
    assert reason is None

    verified, reason = verify_membership("Ставка: 30 PLN/hour. Уточніть деталі?", "FACT", "30 PLN/hour")
    assert verified is False
    assert reason == "format"

    verified, reason = verify_membership("Ставка: 35 PLN gross/hour. Уточніть деталі.", "FACT", "30 PLN/hour")
    assert verified is False
    assert reason == "fact"


def test_a_list_and_a_question_together_cannot_be_attributed():
    assert detect_format("1. Ставка.\n2. Графік.") == "TIPS"
    assert detect_format("Ставка?") == "QUESTION"
    assert detect_format("Ставка 30 PLN/hour. Уточніть деталі.") == "FACT"
    assert detect_format("1. Ставка.\n2. А що ще?") is None


def test_the_deterministic_format_keeps_the_fact():
    text = render_format("TIPS", ["Ставка: 30 PLN/hour.", "Уточніть деталі."], "Напишіть у приватні повідомлення.")
    assert text is not None
    assert "30 PLN/hour" in text
    assert detect_format(text) == "TIPS"


@pytest.mark.asyncio
async def test_the_planner_survives_a_run_without_audience_questions(db, client, monkeypatch):
    # The experiment run has no audience questions. The planner used to crash
    # on that, which turned a missing input into a broken experiment.
    from app.ai import AIService
    from app.config import settings

    vacancy = await _vacancy(client)
    from app.models import Vacancy

    row = await db.get(Vacancy, vacancy["id"])
    captured: dict = {}
    monkeypatch.setattr(settings, "ai_mock_mode", False)

    async def request(self, route, user_content, *args, **kwargs):
        captured["prompt"] = user_content
        return [{
            "hook": "Ставка",
            "content": "Ставка: 30 PLN/hour. Перед виїздом уточніть у роботодавця умови виплат, "
                       "розрахунковий період і дату першої зарплати, щоб перевірити цифру до того, "
                       "як приймете рішення про роботу.",
            "content_type": "FAQ",
            "language": "uk",
            "rationale": "тест",
        }]

    monkeypatch.setattr(AIService, "_request", request)
    ideas = await AIService().plan_content(
        vacancies=[row], audience_questions=None, trend_topics=[], editorial_analysis=None,
        language="uk", brand_voice="factual", count=1,
        format_instruction="FACT: plain statement.", fact_text="30 PLN/hour",
        base_text="Ставка: 30 PLN/hour.", subject_label="PAY",
    )
    assert len(ideas) == 1
    assert "FORMAT EXPERIMENT" in captured["prompt"]
    assert "30 PLN/hour" in captured["prompt"]

