"""Anti-repetition guarantees.

The owner saw published posts that are near-identical (measured pairs up to
1.00 similarity). The cause was the single fixed template per editorial
angle plus a dedupe that only caught exact matches. These tests pin down the
fix:

* the publish gate blocks an identical or near-duplicate text before the
  external call (fail-closed, deterministic);
* the auto-scheduler keeps such a draft back in review instead of queuing it;
* the gate always stops the *younger* copy, never the post already seen;
* two rounds of one format experiment do not block each other (the owner
  approved that repetition on purpose), while an experiment post still
  answers for itself against normal content with a relaxed threshold;
* the deterministic angles rotate wording by day and keep every confirmed
  fact unchanged.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.editorial_angles import angle_post
from app.enums import PostStatus
from app.models import ThreadsPost, Vacancy
from app.post_validation import (
    NEAR_DUPLICATE_RATIO,
    duplicate_gate_mode, text_similarity, validate_post_for_publication,
)
from worker.main import _auto_schedule_post, _substitute_angle, find_duplicate_of, publish_post


def _now():
    return datetime.now(timezone.utc)


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


async def _vacancy(db) -> Vacancy:
    vacancy = Vacancy(
        title="Комплектувальник", description="Складання товару на складі", location="Краків",
        salary="23 зл/год netto", schedule="2 зміни", housing="650 зл/міс",
        contract_type="umowa zlecenie", transport="автобус від зупинки B",
        requirements="документи та мінімальний досвід", language="uk", active=True,
    )
    db.add(vacancy)
    await db.flush()
    return vacancy


def _post(db, vacancy, content, *, status=PostStatus.PUBLISHED, cause=None, hook="Ставка"):
    post = ThreadsPost(
        vacancy_id=vacancy.id, content=content, hook=hook,
        content_type="FAQ", language="uk", status=status,
        content_subject="PAY",
        published_at=_now() - timedelta(days=2) if status == PostStatus.PUBLISHED else None,
        remote_id="remote-published" if status == PostStatus.PUBLISHED else None,
    )
    if cause:
        post.cause_type, post.cause_ref = cause
    db.add(post)
    return post


def test_similarity_gate_verdicts():
    rate = "Ставка: 23 зл/год netto. Перед виїздом уточніть у роботодавця, за який період нараховують суму. Так ви перевірите цифру до того, як приймете рішення."
    tweaked = rate.replace("за який період", "за який період саме")
    housing = "Житло: 650 зл/міс. Уточніть, чи сума за одну людину та чи входить комуналка. Реальний бюджет формується з деталей."
    assert duplicate_gate_mode(rate, rate) == "IDENTICAL"
    assert duplicate_gate_mode(rate, tweaked) == "NEAR"
    assert duplicate_gate_mode(rate, housing) is None
    # The relaxation for experiment posts only forgives a real difference.
    assert duplicate_gate_mode(rate, tweaked, relaxed=True) == "NEAR"


def test_the_calibrated_threshold_matches_the_measured_feed_faults():
    # The published faults the owner saw measured 0.97-1.00; genuinely
    # different posts of the same feed stay well below the threshold.
    assert NEAR_DUPLICATE_RATIO >= 0.78


@pytest.mark.asyncio
async def test_publish_gate_blocks_an_identical_active_post(db):
    vacancy = await _vacancy(db)
    text = angle_post(vacancy, "RATE", "uk")
    _post(db, vacancy, text)
    younger = _post(db, vacancy, text, status=PostStatus.APPROVED)
    await db.commit()
    await db.refresh(younger)

    with pytest.raises(ValueError, match="duplicates active post"):
        await publish_post(db, younger.id)

    assert younger.status == PostStatus.FAILED
    assert "дублирует" in (younger.failure_reason or "")


@pytest.mark.asyncio
async def test_auto_scheduler_keeps_a_near_duplicate_in_review(db):
    vacancy = await _vacancy(db)
    text = angle_post(vacancy, "RATE", "uk")
    older = _post(db, vacancy, text)
    await db.commit()
    await db.refresh(older)
    draft = _post(
        db, vacancy, text.replace("уточніть у роботодавця", "уточніть у роботодавця заздалегідь"),
        status=PostStatus.AI_GENERATED,
    )
    await db.commit()
    await db.refresh(draft)

    scheduled = await _auto_schedule_post(db, draft)

    assert scheduled is False
    assert draft.status == PostStatus.AI_GENERATED
    assert "повторяет" in (draft.reasoning_summary or "")
    assert str(older.id) in (draft.reasoning_summary or "")


@pytest.mark.asyncio
async def test_the_gate_stops_the_younger_copy_only(db):
    vacancy = await _vacancy(db)
    text = angle_post(vacancy, "RATE", "uk")
    first = _post(db, vacancy, text)
    second = _post(db, vacancy, text, status=PostStatus.APPROVED)
    await db.commit()
    await db.refresh(first)
    await db.refresh(second)

    older_view, older_mode = await find_duplicate_of(db, first)
    newer_view, newer_mode = await find_duplicate_of(db, second)

    # The older post is already out; only the younger copy may be stopped.
    assert older_view is None and older_mode is None
    assert newer_view is not None and newer_view.id == first.id
    assert newer_mode == "IDENTICAL"


@pytest.mark.asyncio
async def test_rounds_of_one_experiment_do_not_block_each_other(db):
    vacancy = await _vacancy(db)
    text = angle_post(vacancy, "RATE", "uk")
    _post(db, vacancy, text, cause=("EXPERIMENT", "7:FACT"))
    draft = _post(
        db, vacancy, text,
        status=PostStatus.AI_GENERATED, cause=("EXPERIMENT", "7:QUESTION"),
    )
    await db.commit()
    await db.refresh(draft)

    view, mode = await find_duplicate_of(db, draft)
    assert view is None and mode is None


@pytest.mark.asyncio
async def test_an_experiment_post_is_relaxed_towards_normal_content_only(db):
    """The experiment may repeat its fact; normal content must not."""
    vacancy = await _vacancy(db)
    editorial = angle_post(vacancy, "RATE", "uk")
    experiment_view = " ".join(editorial.split()[:-10])  # shorter: ~0.85 similar
    assert 0.78 <= text_similarity(editorial, experiment_view) < 0.90

    # Normal editorial draft: the full threshold applies and blocks it.
    _post(db, vacancy, editorial)
    normal_draft = _post(db, vacancy, experiment_view, status=PostStatus.AI_GENERATED)
    await db.commit()
    await db.refresh(normal_draft)
    view, mode = await find_duplicate_of(db, normal_draft)
    assert view is not None and mode == "NEAR"

    # The experiment carries the same text with cause markers: relaxed.
    experiment_draft = _post(
        db, vacancy, experiment_view,
        status=PostStatus.AI_GENERATED, cause=("EXPERIMENT", "9:FACT"),
    )
    await db.commit()
    await db.refresh(experiment_draft)
    view, mode = await find_duplicate_of(db, experiment_draft)
    assert view is None and mode is None


@pytest.mark.asyncio
async def test_editorial_content_near_an_experiment_post_is_blocked(db):
    """The relaxation belongs to the experiment side, not to normal posts."""
    vacancy = await _vacancy(db)
    editorial = angle_post(vacancy, "RATE", "uk")
    _post(db, vacancy, editorial, cause=("EXPERIMENT", "11:FACT"))
    draft = _post(db, vacancy, " ".join(editorial.split()[:-10]), status=PostStatus.AI_GENERATED)
    await db.commit()
    await db.refresh(draft)

    view, mode = await find_duplicate_of(db, draft)
    assert view is not None and mode == "NEAR"


@pytest.mark.asyncio
async def test_rate_variants_differ_but_keep_the_confirmed_fact(db):
    vacancy = await _vacancy(db)
    versions = [angle_post(vacancy, "RATE", "uk", variant=index) for index in range(3)]
    assert all(versions)
    assert len(set(versions)) == 3
    for text in versions:
        assert "23 зл/год netto" in text
        assert len(text) <= 500
        validate_post_for_publication(content=text, language="uk", vacancy=vacancy, content_type="FAQ")
    # Each variant is safe against the previous one: publishing them on
    # consecutive days no longer registers as a repetition.
    for index in range(len(versions) - 1):
        assert duplicate_gate_mode(versions[index], versions[index + 1]) is None


@pytest.mark.asyncio
async def test_every_angle_has_a_fact_preserving_variant(db):
    vacancy = await _vacancy(db)
    pairs = {
        "RATE": "23 зл/год netto",
        "SCHEDULE": "2 зміни",
        "HOUSING": "650 зл/міс",
        "CONTRACT": "umowa zlecenie",
        "CITY": "Краків",
        "TRANSPORT": "автобус від зупинки B",
        "REQUIREMENTS": "документи та мінімальний досвід",
    }
    for angle, fact in pairs.items():
        original = angle_post(vacancy, angle, "uk", variant=0)
        rotated = angle_post(vacancy, angle, "uk", variant=1)
        assert original and rotated, angle
        assert rotated != original
        assert fact in rotated
        assert duplicate_gate_mode(original, rotated) is None


@pytest.mark.asyncio
async def test_substitute_angle_rotates_the_day(db):
    vacancy = await _vacancy(db)
    normalise = _normalise
    rate_day = _substitute_angle([vacancy], "uk", "", set(), normalise, variant=0)
    schedule_day = _substitute_angle([vacancy], "uk", "", set(), normalise, variant=1)
    assert rate_day is not None and schedule_day is not None
    assert rate_day[4] == "PAY"
    assert schedule_day[4] == "SCHEDULE"
    # The two days answer different conditions, not the same text twice.
    assert duplicate_gate_mode(rate_day[0], schedule_day[0]) is None


@pytest.mark.asyncio
async def test_old_posts_leave_the_gate_window_in_time(db):
    """An old post must not block a new related post forever."""
    vacancy = await _vacancy(db)
    text = angle_post(vacancy, "RATE", "uk")
    stale = _post(db, vacancy, text)
    await db.commit()
    await db.refresh(stale)
    stale.created_at = _now() - timedelta(days=20)
    await db.commit()

    draft = _post(db, vacancy, text, status=PostStatus.AI_GENERATED)
    await db.commit()
    await db.refresh(draft)

    # The stale post is outside the gate window: a fresh draft of the same
    # angle is not blocked by a post from three weeks ago.
    view, mode = await find_duplicate_of(db, draft)
    assert view is None and mode is None
