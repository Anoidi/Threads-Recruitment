"""Regression tests for autonomous content generation.

Background: the daily plan produced nothing for weeks. Three causes were
found: the planner asked for several posts in one completion (impossible for a
4B model inside the token budget), the route list contained cloud deployments
without API keys, and a repeated angle was dropped instead of replaced. The
planner now works in small batches and a duplicate is replaced with a
deterministic angle built from confirmed vacancy facts.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import worker.main as worker_main
import worker.planner as worker_planner
from app.ai import PLANNER_BATCH_SIZE, AIService
from app.config import settings
from app.content_quality import render_vacancy_post
from app.content_subjects import detect_subjects, primary_subject, subjects_of
from app.editorial_angles import EDITORIAL_ANGLES, angle_content_type, angle_hook, angle_post, angle_subject
from app.enums import PostStatus
from app.models import ActivityEvent, AppSetting, ThreadsPost, Vacancy
from app.post_validation import validate_post_for_publication
from worker.main import _substitute_angle, content_plan


def _vacancy(**overrides) -> Vacancy:
    values = dict(
        title="Працівник складу",
        description="Комплектація замовлень на сучасному складі.",
        location="Kraków",
        salary="30.50 gross/hour",
        student_salary="25.00 net/hour",
        currency="PLN",
        schedule="2 shifts, 8 hours",
        contract_type="Umowa zlecenie",
        housing="Shared room, 650 PLN/month",
        housing_cost="650 PLN/month",
        transport="Company transport",
        requirements="Adult applicants",
        contact="demo@example.invalid",
        language="uk",
        active=True,
    )
    values.update(overrides)
    return Vacancy(**values)


@pytest.mark.parametrize("angle", EDITORIAL_ANGLES)
def test_every_angle_is_grounded_and_publishable(angle):
    vacancy = _vacancy()
    text = angle_post(vacancy, angle, "uk")
    assert text, f"angle {angle} produced no text"
    assert len(text) <= 500
    # The final publish gate must accept the deterministic angle.
    validate_post_for_publication(
        content=text, language="uk", vacancy=vacancy, content_type=angle_content_type(angle)
    )


def test_angle_needs_its_own_fact():
    vacancy = _vacancy(schedule=None, housing=None, housing_cost=None, contract_type=None)
    assert angle_post(vacancy, "SCHEDULE", "uk") is None
    assert angle_post(vacancy, "HOUSING", "uk") is None
    assert angle_post(vacancy, "CONTRACT", "uk") is None
    # The rate and the city are still confirmed.
    assert angle_post(vacancy, "RATE", "uk")
    assert angle_post(vacancy, "CITY", "uk")


def test_angle_does_not_invent_a_condition_the_vacancy_lacks():
    vacancy = _vacancy(transport=None, housing=None, housing_cost=None)
    text = angle_post(vacancy, "RATE", "uk")
    assert "житл" not in text.lower()
    assert "транспорт" not in text.lower()


def test_angle_hook_is_the_first_sentence():
    vacancy = _vacancy()
    text = angle_post(vacancy, "RATE", "uk")
    assert angle_hook(text).startswith("Ставка")
    assert len(angle_hook(text)) <= 500


@pytest.mark.asyncio
async def test_duplicate_idea_is_replaced_by_a_source_grounded_angle(db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()

    seen = {" ".join(render_vacancy_post(vacancy, "uk").split()).casefold()}
    substitute = _substitute_angle([vacancy], "uk", "", seen, lambda value: " ".join(value.split()).casefold())

    assert substitute is not None
    content, content_type, hook, vacancy_id, subject = substitute
    assert vacancy_id == vacancy.id
    assert content_type in {"FAQ", "EDUCATIONAL"}
    assert hook
    assert subject == angle_subject("RATE")
    assert content not in seen


class _FrozenPlanDate(datetime):
    """Pins the planner's substitute-angle rotation so assertions are date-independent.

    ``_substitute_angle`` rotates the angle order by ``tm_yday % len(EDITORIAL_ANGLES)``;
    on some days the rotated order never reaches the RATE/SCHEDULE angles, so a test
    that expects a specific angle's wording fails purely because of the calendar.
    Freezing the planner clock keeps the deterministic rotation while making the test
    stable: 2026-04-01 → 91 % 7 == 0 → the RATE angle comes first.
    """

    @classmethod
    def now(cls, tz=None):  # noqa: N802
        fixed = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
        if tz is not None:
            return fixed.astimezone(tz)
        return fixed


@pytest.mark.asyncio
async def test_plan_creates_drafts_even_when_every_proposal_is_a_duplicate(db, monkeypatch):
    """A repeated proposal must not leave the day empty."""
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    # An identical post already exists, so the model proposal is a duplicate.
    existing = ThreadsPost(
        content="Що зараз обговорюють шукачі роботи. Порівняйте три речі.",
        content_type="ENGAGEMENT", language="uk", status=PostStatus.PUBLISHED,
    )
    db.add(existing)
    await db.commit()

    class DuplicatePlanner:
        async def plan_content(self, **kwargs):
            return [{
                "hook": "Дубль",
                "content": "Що зараз обговорюють шукачі роботи. Порівняйте три речі.",
                "content_type": "ENGAGEMENT",
                "language": "uk",
                "rationale": "Повтор",
                "vacancy_id": None,
            }]

    monkeypatch.setattr(worker_main, "AIService", lambda: DuplicatePlanner())
    monkeypatch.setattr(worker_main, "_auto_schedule_post", lambda *a, **k: _noop())
    monkeypatch.setattr(worker_planner, "datetime", _FrozenPlanDate)

    summary = await content_plan(db, {"count": 2, "language": "uk"})

    event = (await db.execute(
        select(ActivityEvent).where(ActivityEvent.action == "content_plan_generated")
    )).scalar_one()
    assert event.detail["skipped"] >= 1
    assert event.detail["substituted"] >= 1
    assert event.detail["created"] >= 1
    drafts = list((await db.execute(
        select(ThreadsPost).where(ThreadsPost.status == PostStatus.AI_GENERATED)
    )).scalars())
    assert drafts
    assert any(draft.content_subject == "PAY" for draft in drafts)
    assert "source-grounded angle" in summary or "replaced" in summary


async def _noop():
    return False


def test_every_angle_owns_a_distinct_subject():
    subjects = [angle_subject(angle) for angle in EDITORIAL_ANGLES]
    assert len(set(subjects)) == len(subjects), subjects


def test_uk_wording_pools_keep_the_duplicate_gate_margin():
    """Every uk wording of an angle must clear the publish duplicate gate.

    The pool is what lets one subject repeat across days (the operator's ×3
    request) without producing IDENTICAL/NEAR pairs: variant 0 renders from
    the original template, the extras rotate on later days. Placeholders are
    identical across wordings, so the template-level check is slightly more
    conservative than the rendered one.
    """
    from app.editorial_angles import _TEMPLATES, _TEMPLATE_VARIANTS
    from app.post_validation import duplicate_gate_mode

    for angle in EDITORIAL_ANGLES:
        pool = [_TEMPLATES[angle]["uk"], *(_TEMPLATE_VARIANTS.get(angle, {}).get("uk") or [])]
        for index, wording in enumerate(pool):
            for other in pool[index + 1:]:
                assert duplicate_gate_mode(wording, other) is None, (angle, wording, other)
    # The angles the operator repeats (×3) need at least three safe wordings.
    for angle in ("RATE", "SCHEDULE", "CONTRACT", "CITY", "TRANSPORT"):
        assert len(_TEMPLATE_VARIANTS[angle]["uk"]) >= 2, angle


def test_subject_detection_reads_the_condition_a_post_talks_about():
    housing = "Житло вираховується з зарплати: 600–650 зл/міс. Це означає, що виплата не випадає на руки."
    pay = "Ставка 30,50 зл/год netto. Для студентів 35 зл/год."
    schedule = "Графік: 2 зміни, по 8 годин."
    contract = "Договір: umowa zlecenie."

    assert primary_subject(housing) == "HOUSING"
    assert primary_subject(pay) == "PAY"
    assert primary_subject(schedule) == "SCHEDULE"
    assert primary_subject(contract) == "CONTRACT"
    assert "HOUSING" in detect_subjects(housing)


def test_subject_detection_separates_words_from_mentions():
    # The same facts, different wording, must land on the same subject.
    first = "У вакансіях у Вічліцях та Кракові житло вираховується з зарплати, 600–650 зл/міс."
    second = "У вакансіях в Кракові та Вічліцях для студентів ставка до 35 зл/год (netto), але житло 600–650 зл/міс вираховується з зарплати."
    assert primary_subject(first) == primary_subject(second) == "HOUSING"


@pytest.mark.asyncio
async def test_plan_refuses_two_posts_with_the_same_subject(db, monkeypatch):
    """Six posts about one condition are one post six times."""
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    vacancy = _vacancy()
    db.add(vacancy)
    await db.commit()

    housing_text = (
        "У вакансіях у Вічліцях та Кракові житло вираховується з зарплати, 600–650 зл/міс. "
        "Це означає, що виплата не випадає на руки. Запитайте, скільки коштує житло."
    )
    pay_text = (
        "Ставка 30,50 зл/год netto для студентів. Уточніть, за який період і коли приходить перша зарплата."
    )

    class SameSubjectPlanner:
        async def plan_content(self, **kwargs):
            return [
                {"hook": "Житло", "content": housing_text, "content_type": "HOUSING_DISCUSSION",
                 "language": "uk", "rationale": "Про житло", "vacancy_id": None},
                {"hook": "Житло ще раз", "content": housing_text + " Уточніть умови.",
                 "content_type": "HOUSING_DISCUSSION", "language": "uk", "rationale": "Знову про житло", "vacancy_id": None},
                {"hook": "Ставка", "content": pay_text, "content_type": "SALARY_DISCUSSION",
                 "language": "uk", "rationale": "Про ставку", "vacancy_id": None},
            ]

    monkeypatch.setattr(worker_main, "AIService", lambda: SameSubjectPlanner())
    monkeypatch.setattr(worker_main, "_auto_schedule_post", lambda *a, **k: _noop())

    await content_plan(db, {"count": 3, "language": "uk"})

    drafts = list((await db.execute(
        select(ThreadsPost).where(ThreadsPost.status == PostStatus.AI_GENERATED)
    )).scalars())
    # The planner guarantees distinct recorded subjects (``used_subjects``);
    # recomputing the subject with the keyword heuristic instead would make
    # this day-dependent: the substitute angle rotates by ``tm_yday % 7``, and
    # on some days the chosen angle's text innocently mentions another
    # condition (the transport template mentions hostel housing), so
    # ``primary_subject`` could collide with an idea's subject.
    subjects = [draft.content_subject for draft in drafts]
    assert None not in subjects, subjects
    assert len(subjects) == len(set(subjects)), subjects
    assert "HOUSING" in subjects
    assert "PAY" in subjects


@pytest.mark.asyncio
async def test_plan_uses_bounded_batches(db, monkeypatch):
    """The planner must not ask one completion for everything at once."""
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    requested: list[int] = []

    class Recorder(AIService):
        async def _plan_batch(self, **kwargs):
            requested.append(kwargs["count"])
            return [{
                "hook": "Гак",
                "content": "Ставка 30 зл/год. Це загальне оголошення для перевірки умінь. " * 3,
                "content_type": "FAQ",
                "language": "uk",
                "rationale": "Приклад",
                "vacancy_id": None,
            }]

    ideas = await Recorder().plan_content(
        vacancies=[], audience_questions=[], trend_topics=[], editorial_analysis=None,
        language="uk", brand_voice="x", count=6,
    )
    # Six ideas are requested in batches of two, and never in one completion.
    assert requested == [PLANNER_BATCH_SIZE] * 3
    assert all(size <= PLANNER_BATCH_SIZE for size in requested)
    assert len(ideas) == 3
