"""Regression tests for the replier agent and the cause attribution.

Two gaps motivated these. The community answered every candidate question
with the same sentence, so a real question got no real answer. And nothing
linked a published post to the reason it was published, so "what resonates"
could not be attributed to anything.
"""
import pytest
from sqlalchemy import select

import worker.main as worker_main
from app.enums import AgentRole, PostStatus
from app.models import (
    ActivityEvent, AgentConfig, ThreadsInteraction, ThreadsPost, Vacancy,
)


def _vacancy(**overrides) -> Vacancy:
    values = dict(
        title="Працівник складу",
        description="Комплектація замовлень.",
        location="Kraków",
        salary="30,50 zł/год netto",
        student_salary="35,00 zł/год netto для студентів",
        currency="PLN",
        schedule="2 зміни, 8 годин",
        contract_type="umowa zlecenie",
        housing="Кімната на двох, 650 zł/міс",
        housing_cost="650 zł/міс",
        transport="Корпоративний транспорт",
        requirements="Без досвіду",
        contact="demo@example.invalid",
        language="uk",
        active=True,
    )
    values.update(overrides)
    return Vacancy(**values)


def _interaction(message: str, classification: str = "HOUSING_QUESTION", post_id: int | None = None) -> ThreadsInteraction:
    return ThreadsInteraction(
        remote_id="reply-" + message[:16], kind="REPLY", username="candidate",
        message=message, classification=classification, risk="LOW",
        needs_review=True, post_id=post_id,
    )


@pytest.mark.asyncio
async def test_every_agent_role_has_a_model_route():
    from app.ai import MODEL_ROUTE

    for role in AgentRole:
        assert role in MODEL_ROUTE, f"no model route for {role}"


@pytest.mark.asyncio
async def test_agent_configs_include_the_replier(db):
    from app.api import ensure_agent_configs

    await ensure_agent_configs(db)

    roles = set((await db.execute(select(AgentConfig.role))).scalars())
    assert AgentRole.REPLIER in roles


@pytest.mark.asyncio
async def test_replier_writes_a_specific_answer_from_confirmed_facts(db, monkeypatch):
    from worker.main import draft_replies

    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    post = ThreadsPost(
        vacancy_id=vacancy.id, content="Vacancy", content_type="VACANCY",
        language="uk", status=PostStatus.PUBLISHED, remote_id="post-1",
    )
    db.add(post)
    await db.flush()
    db.add(_interaction("Чи є житло і скільки коштує?", post_id=post.id))
    await db.commit()

    asked: list[str] = []

    class FakeService:
        async def draft_reply(self, question, facts, language="uk"):
            asked.append(question)
            assert any("650" in fact for fact in facts), facts
            return "Житло передбачене: кімната на двох, 650 zł/міс. Це вираховується із зарплати."

    monkeypatch.setattr("app.ai.AIService", lambda: FakeService())

    summary = await draft_replies(db)

    assert asked, "the replier did not receive the question"
    assert "1" in summary
    from app.models import ReplyDraft
    drafts = list((await db.execute(select(ReplyDraft))).scalars())
    assert len(drafts) == 1
    assert "650" in drafts[0].content


@pytest.mark.asyncio
async def test_replier_skips_questions_that_already_have_an_answer(db, monkeypatch):
    from app.models import ReplyDraft
    from worker.main import draft_replies

    db.add(_interaction("Чи є житло?"))
    await db.commit()
    interaction = (await db.execute(select(ThreadsInteraction))).scalar_one()
    db.add(ReplyDraft(interaction_id=interaction.id, content="Уже есть ответ"))
    await db.commit()

    calls: list[str] = []

    class FakeService:
        async def draft_reply(self, question, facts, language="uk"):
            calls.append(question)
            return "Ответ"

    monkeypatch.setattr("app.ai.AIService", lambda: FakeService())

    summary = await draft_replies(db)

    assert calls == []
    assert "0" in summary


@pytest.mark.asyncio
async def test_a_planned_post_records_why_it_was_published(db, monkeypatch):
    """A post without a cause cannot be attributed to anything."""
    from worker.main import content_plan

    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    await db.commit()

    question = _interaction("Яка реальна зарплата netto?")
    db.add(question)
    await db.commit()
    question_id = (await db.execute(select(ThreadsInteraction))).scalar_one().id

    class Planner:
        async def plan_content(self, **kwargs):
            return [{
                "hook": "Ставка",
                "content": "Ставка 30,50 zł/год netto. Уточніть, за який період і коли приходить зарплата.",
                "content_type": "SALARY_DISCUSSION",
                "language": "uk",
                "rationale": "Відповідає на питання про зарплату.",
                "vacancy_id": None,
                "source_question_index": 0,
            }]

    monkeypatch.setattr(worker_main, "AIService", lambda: Planner())
    monkeypatch.setattr(worker_main, "_auto_schedule_post", lambda *a, **k: _noop())

    await content_plan(db, {"count": 1, "language": "uk"})

    post = (await db.execute(select(ThreadsPost).where(ThreadsPost.status == PostStatus.AI_GENERATED))).scalar_one()
    assert post.cause_type == "AUDIENCE_QUESTION"
    assert post.cause_ref == str(question_id)
    assert post.hypothesis and "зарплат" in post.hypothesis.lower()


async def _noop():
    return False
