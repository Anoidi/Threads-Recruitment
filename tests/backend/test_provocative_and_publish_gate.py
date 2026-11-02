import pytest
from fastapi import HTTPException

from app.ai import AIService
from app.content_quality import structured_vacancy_facts
from app.enums import ContentType, PostStatus
from app.models import ThreadsPost, Vacancy
from app.post_validation import validate_post_for_publication
from worker.main import publish_post


def make_vacancy() -> Vacancy:
    return Vacancy(
        title="Працівник складу",
        description="Комплектація замовлень",
        location="Краків",
        salary="23 зл/год netto",
        student_salary="31,40 зл/год netto",
        currency="PLN",
        schedule="6 днів на тиждень",
        contract_type="umowa zlecenie",
        housing="650 зл/міс.",
        contact=None,
        language="uk",
    )


@pytest.mark.asyncio
async def test_provocative_mode_returns_three_bounded_ukrainian_variants():
    variants = await AIService().generate_provocative_variants(make_vacancy())
    assert [item["style"] for item in variants] == ["умеренный", "саркастический", "дискуссионный"]
    assert len(variants) == 3
    assert all(0 < len(item["content"]) <= 500 for item in variants)
    assert all(item["content"].endswith("?") for item in variants)
    assert all("23 зл/год" in item["content"] for item in variants)
    assert all("31,40 зл/год" in item["content"] for item in variants)


def test_structured_vacancy_source_excludes_unresolved_placeholders():
    vacancy = make_vacancy()
    vacancy.location = "Не вказано в оголошенні"
    facts = structured_vacancy_facts(vacancy)
    assert facts.location is None


def test_publish_gate_rejects_unconfirmed_rate_and_placeholder():
    vacancy = make_vacancy()
    with pytest.raises(HTTPException, match="неподтверждённую ставку"):
        validate_post_for_publication(
            content="Працівник складу у Кракові. Оплата: 99 зл/год netto. Що думаєте?",
            language="uk",
            vacancy=vacancy,
            content_type=ContentType.PROVOCATIVE_RECRUITMENT,
        )
    with pytest.raises(HTTPException, match="шаблон"):
        validate_post_for_publication(
            content="Працівник складу у Кракові. Не вказано в оголошенні. Напишіть у приватні повідомлення.",
            language="uk",
            vacancy=vacancy,
            content_type=ContentType.VACANCY,
        )


def test_publish_gate_blocks_incomplete_vacancy_city_but_draft_can_exist():
    vacancy = make_vacancy()
    vacancy.location = "Не вказано в оголошенні"
    with pytest.raises(HTTPException, match="немає міста"):
        validate_post_for_publication(
            content="Працівник складу. Оплата: 23 зл/год netto. Напишіть у приватні повідомлення.",
            language="uk",
            vacancy=vacancy,
            content_type=ContentType.VACANCY,
        )


def test_publish_gate_accepts_localized_city_form():
    vacancy = make_vacancy()
    validate_post_for_publication(
        content="Працівник складу в Кракові. Оплата: 23 зл/год netto. Напишіть у приватні повідомлення.",
        language="uk",
        vacancy=vacancy,
        content_type=ContentType.VACANCY,
    )
@pytest.mark.asyncio
async def test_worker_marks_invalid_approved_post_failed_before_publish(db):
    vacancy = make_vacancy()
    db.add(vacancy)
    await db.flush()
    post = ThreadsPost(
        vacancy_id=vacancy.id,
        content="Працівник складу у Кракові. Оплата: 99 зл/год netto.",
        content_type=ContentType.VACANCY,
        language="uk",
        status=PostStatus.APPROVED,
    )
    db.add(post)
    await db.commit()
    with pytest.raises(HTTPException):
        await publish_post(db, post.id, "APPROVED", post.version)
    await db.refresh(post)
    assert post.status == PostStatus.FAILED
    assert "ставку" in (post.failure_reason or "")
