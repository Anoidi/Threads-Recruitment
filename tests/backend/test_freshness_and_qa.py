"""Regression tests for the two checks that guard the automatic mode.

Vacancy freshness answers "are the published conditions still true" and model
QA answers "does the text read correctly and stay inside the facts". Both are
one-sided: freshness can block a publication, and the model review can block
a post but never clear one.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.enums import PostStatus
from app.model_qa import (
    AMBIGUOUS, LANGUAGE_WRONG, MISLEADING_PROMISE, UNSUPPORTED_CLAIM, QaResult,
    review_post,
)
from app.models import ActivityEvent, ThreadsPost, Vacancy
from app.vacancy_freshness import (
    DRIFTED, FRESH, STALE, UNVERIFIED, record_confirmation, record_drift,
    vacancy_freshness,
)


def _vacancy(**overrides) -> Vacancy:
    values = dict(
        title="Працівник складу", description="Опис.", location="Kraków",
        salary="30,50 zł/год netto", currency="PLN", schedule="2 зміни",
        contract_type="umowa zlecenie", language="uk", active=True,
    )
    values.update(overrides)
    return Vacancy(**values)


# --- vacancy freshness -----------------------------------------------------

def test_an_unconfirmed_source_is_not_trusted():
    freshness = vacancy_freshness(_vacancy())
    assert freshness.status == UNVERIFIED
    assert freshness.trusted is False
    assert freshness.needs_check is True


def test_a_recently_confirmed_source_is_fresh():
    vacancy = _vacancy()
    record_confirmation(vacancy)
    freshness = vacancy_freshness(vacancy)
    assert freshness.status == FRESH
    assert freshness.trusted is True
    assert freshness.age_days == 0


def test_a_source_older_than_the_limit_needs_a_check():
    vacancy = _vacancy()
    vacancy.source_confirmed_at = datetime.now(timezone.utc) - timedelta(days=20)
    freshness = vacancy_freshness(vacancy)
    assert freshness.status == STALE
    assert freshness.trusted is False
    assert freshness.needs_check is True


def test_a_changed_source_is_drift_and_outranks_the_age():
    vacancy = _vacancy()
    record_confirmation(vacancy)
    record_drift(vacancy, "salary: 30,50 → 32,00")
    freshness = vacancy_freshness(vacancy)
    assert freshness.status == DRIFTED
    assert freshness.trusted is False
    assert "32,00" in freshness.reason


def test_confirming_again_clears_the_drift():
    vacancy = _vacancy()
    record_drift(vacancy, "salary changed")
    record_confirmation(vacancy)
    assert vacancy_freshness(vacancy).status == FRESH


# --- model QA --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_passing_review_is_accepted(monkeypatch):
    from app.ai import AIService

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        return '{"verdict": "PASS", "issues": [], "note": "Текст коректний."}', "stop"

    monkeypatch.setattr(AIService, "_chat", chat)
    result = await review_post("Ставка 30,50 zł/год netto.", ["Ставка: 30,50 zł/год netto"], language="uk")
    assert result.passed is True
    assert result.issues == ()


@pytest.mark.asyncio
async def test_a_failing_review_blocks_and_explains(monkeypatch):
    from app.ai import AIService

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        return '{"verdict": "FAIL", "issues": ["UNSUPPORTED_CLAIM"], "note": "Ставка 40 zł не підтверджена."}', "stop"

    monkeypatch.setattr(AIService, "_chat", chat)
    result = await review_post("Ставка 40 zł/год.", ["Ставка: 30,50 zł/год netto"], language="uk")
    assert result.passed is False
    assert result.issues == (UNSUPPORTED_CLAIM,)
    assert "не підтверджена" in result.summary or "conditions" in result.summary


@pytest.mark.asyncio
async def test_a_broken_review_does_not_pass(monkeypatch):
    """The review may only block. A broken review is not a pass."""
    from app.ai import AIService

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        raise RuntimeError("model is unreachable")

    monkeypatch.setattr(AIService, "_chat", chat)
    result = await review_post("Будь-який текст", ["Ставка: 30 zł"], language="uk")
    assert result.passed is False
    assert result.reason


@pytest.mark.asyncio
async def test_an_unusable_answer_does_not_pass(monkeypatch):
    from app.ai import AIService

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        return "не JSON вовсе", "stop"

    monkeypatch.setattr(AIService, "_chat", chat)
    result = await review_post("Будь-який текст", [], language="uk")
    assert result.passed is False


@pytest.mark.asyncio
async def test_a_failure_without_a_named_reason_still_blocks(monkeypatch):
    from app.ai import AIService

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        return '{"verdict": "FAIL", "issues": [], "note": "щось не так"}', "stop"

    monkeypatch.setattr(AIService, "_chat", chat)
    result = await review_post("Текст", [], language="uk")
    assert result.passed is False
    assert result.issues == (AMBIGUOUS,)


# --- the two checks in the publication path --------------------------------

@pytest.mark.asyncio
async def test_auto_mode_publishes_only_after_a_passed_review(client, db, monkeypatch):
    """Auto may not publish a post whose review failed."""
    from app.ai import AIService
    from tests.backend.conftest import set_autonomy_mode

    await set_autonomy_mode(db, "AUTO")
    db.add(__import__("app.models", fromlist=["ThreadsAccount"]).ThreadsAccount(
        profile_id="auto-profile", username="recruiter", connected=True, is_mock=False,
        permissions=["threads_basic", "threads_content_publish"],
    ))
    await db.commit()

    async def chat(self, route, content, max_tokens, json_mode=False, timeout=None):
        return '{"verdict": "FAIL", "issues": ["LANGUAGE_WRONG"], "note": "не та мова"}', "stop"

    monkeypatch.setattr(AIService, "_chat", chat)

    post = (await client.post("/api/threads/posts", json={
        "content": "Ставка 30,50 zł/год netto", "content_type": "EDUCATIONAL",
        "idempotency_key": "qa-block-1",
    })).json()
    await client.post(f"/api/threads/posts/{post['id']}/review")
    await client.post(f"/api/threads/posts/{post['id']}/approve", json={"expected_version": post["version"]})
    await db.commit()

    from worker.main import publish_post

    with pytest.raises(Exception):
        await publish_post(db, post["id"], "SCHEDULED", post["version"])

    stored = await db.get(ThreadsPost, post["id"])
    await db.refresh(stored)
    assert stored.model_qa_passed is False
    assert stored.model_qa_at is not None
    assert stored.status == PostStatus.APPROVED


@pytest.mark.asyncio
async def test_pre_schedule_review_receives_contact_and_blocks_queue(db, monkeypatch):
    from app.ai import AIService
    from app.models import Vacancy, BackgroundJob
    from app.model_qa import prepare_post_qa
    from tests.backend.conftest import set_autonomy_mode
    from sqlalchemy import select

    await set_autonomy_mode(db, "AUTO")
    vacancy = Vacancy(title="Warehouse", description="Packing", location="Warsaw",
                      contact="+48 577 017 187", language="uk", source_confirmed_at=datetime.now(timezone.utc))
    db.add(vacancy)
    await db.flush()
    post = ThreadsPost(content="Перш ніж погоджуватися на роботу, уточніть умови та поставте запитання рекрутеру. Зателефонуйте +48 577 017 187, щоб обговорити наступні кроки та зібрати потрібну інформацію.", content_type="EDUCATIONAL", vacancy_id=vacancy.id,
                       language="uk", status=PostStatus.AI_GENERATED)
    db.add(post)
    await db.flush()
    calls = []

    async def chat(self, route, content, max_tokens, **kwargs):
        calls.append(content)
        assert "Контакт: +48 577 017 187" in content
        return '{"verdict":"FAIL","issues":["MACHINE_TONE"],"note":"Rewrite"}', "stop"

    monkeypatch.setattr(AIService, "_chat", chat)
    assert not await prepare_post_qa(db, post)
    assert post.status == PostStatus.AI_GENERATED
    assert post.model_qa_at is not None
    assert not list((await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "publish_post"))).scalars())
    assert not await prepare_post_qa(db, post)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_edit_invalidates_pre_schedule_review(client, db):
    from app.models import ThreadsPost
    from datetime import datetime, timezone

    post = ThreadsPost(content="Old draft", language="uk", status=PostStatus.DRAFT,
                       model_qa_passed=True, model_qa_at=datetime.now(timezone.utc),
                       model_qa_notes="PASS")
    db.add(post)
    await db.commit()
    response = await client.patch(f"/api/threads/posts/{post.id}", json={"content": "Updated draft"})
    assert response.status_code == 200
    await db.refresh(post)
    assert post.model_qa_passed is False
    assert post.model_qa_at is None
