"""Regression tests: the daily planning loop must actually close its feedback loops.

Three silent bugs made the generated feed worse over time:

1. Confirmed editorial preferences (the operator's «write shorter, more
   conversational» feedback) were injected only into the manual generation
   path. The daily planner built a fresh ``AIService`` and never received
   them, so the automatic feed could not adapt to the operator's taste.
2. ``content_evidence`` scanned published posts of all time while its
   report claimed a 30-day window. A frozen "what worked" report kept
   pushing the same winning theme every day until the duplicate gate
   blocked it and only deterministic filler remained.
3. ``covered_subjects`` counted every recent post regardless of status and
   without any time window. Drafts, substitutes and blocked duplicates
   consumed the subject space, so the daily prompt said that every angle was
   already covered even though nothing new had actually been published.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import worker.main as worker_main
from app.config import settings
from app.content_evidence import content_evidence
from app.enums import PostStatus
from app.models import ActivityEvent, AppSetting, PostMetric, ThreadsPost
from worker.main import content_plan


async def _noop(*args, **kwargs):
    return False


class _RecordingPlanner:
    """Stub planner that records the arguments it was called with."""

    def __init__(self, ideas=None):
        self.calls: list[dict] = []
        self.ideas = ideas

    async def plan_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.ideas or [{
            "hook": "Питання до читачів",
            "content": (
                "Яке місто ви обираєте для роботи наступного місяця? Пишіть у коментарях, "
                "а рекрутер відповість у наступному дописі на прикладах вакансій."
            ),
            "content_type": "ENGAGEMENT",
            "language": "uk",
            "rationale": "Перевірка",
            "vacancy_id": None,
        }]


def _planner_factory(captured: dict):
    def factory():
        instance = _RecordingPlanner()
        captured["instance"] = instance
        return instance
    return factory


@pytest.mark.asyncio
async def test_daily_plan_receives_confirmed_editorial_rules(db, monkeypatch):
    """The worker planner must inject confirmed editorial preferences too."""
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    db.add(ActivityEvent(
        action="post_editorial_feedback", entity_id=1,
        detail={"category": "shorter", "language": "uk", "content_type": "FAQ"},
    ))
    db.add(AppSetting(key="editorial_memory_decisions", value={"uk:FAQ:shorter": "CONFIRMED"}))
    await db.commit()

    captured: dict = {}
    monkeypatch.setattr(worker_main, "AIService", _planner_factory(captured))
    monkeypatch.setattr(worker_main, "_auto_schedule_post", _noop)

    await content_plan(db, {"count": 1, "language": "uk"})

    instance = captured.get("instance")
    assert instance is not None, "the daily planner never instantiated the AI service"
    rules = getattr(instance, "editorial_rules", None)
    assert rules, "confirmed editorial preferences never reach the daily planner"
    assert any("коротше" in rule["rule"] for rule in rules)


@pytest.mark.asyncio
async def test_content_evidence_respects_the_window(db):
    """Posts published outside the window must not freeze the recommendation."""
    now = datetime.now(timezone.utc)
    old = ThreadsPost(
        content="Ставка 30 зл/год netto для студентів на складі.",
        content_type="EDUCATIONAL", language="uk", status=PostStatus.PUBLISHED,
        remote_id="remote-old-1", content_subject="PAY",
        published_at=now - timedelta(days=45),
    )
    fresh = ThreadsPost(
        content="Домовилось про житло: спальна кімната коштує 650 зл на місяць.",
        content_type="EDUCATIONAL", language="uk", status=PostStatus.PUBLISHED,
        remote_id="remote-fresh-1", content_subject="HOUSING",
        published_at=now - timedelta(days=2),
    )
    db.add_all([old, fresh])
    await db.flush()
    db.add(PostMetric(post_id=old.id, views=50000, likes=0, replies=100, reposts=0, quotes=0,
                      captured_at=datetime.now(timezone.utc)))
    await db.commit()

    evidence = await content_evidence(db, days=30)

    names = {row["name"] for row in evidence["by_theme"]}
    assert evidence["window_days"] == 30
    assert "PAY" not in names, "a post published outside the window froze the report"
    assert "HOUSING" in names


@pytest.mark.asyncio
async def test_covered_subjects_count_only_recent_published_posts(db, monkeypatch):
    """Drafts and blocked duplicates must not squeeze the angle space."""
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    drafts = [
        ThreadsPost(content="Житло вираховується із зарплати: 600–650 зл/міс.",
                    content_type="FAQ", language="uk", status=PostStatus.AI_GENERATED),
        ThreadsPost(content="Ставка 30,50 зл/год netto для студентів на складі.",
                    content_type="FAQ", language="uk", status=PostStatus.AI_GENERATED),
        ThreadsPost(content="Графік: 2 зміни, по 8 годин.",
                    content_type="FAQ", language="uk", status=PostStatus.AI_GENERATED),
        ThreadsPost(content="Договір: umowa zlecenie на повний місяць.",
                    content_type="FAQ", language="uk", status=PostStatus.AI_GENERATED),
        ThreadsPost(content="У вакансіях у Кракові житло 650 зл/міс вираховується із зарплати.",
                    content_type="FAQ", language="uk", status=PostStatus.FAILED),
    ]
    db.add_all(drafts)
    await db.commit()

    captured: dict = {}
    monkeypatch.setattr(worker_main, "AIService", _planner_factory(captured))
    monkeypatch.setattr(worker_main, "_auto_schedule_post", _noop)

    await content_plan(db, {"count": 1, "language": "uk"})

    instance = captured.get("instance")
    assert instance is not None, "the daily planner never instantiated the AI service"
    covered = instance.calls[0].get("covered_subjects")
    assert covered == [], "unpublished drafts still shrink the planner's angle space"


@pytest.mark.asyncio
async def test_covered_subjects_still_see_published_posts(db, monkeypatch):
    """Published posts must keep steering the planner away from repeats."""
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "ai_mock_mode", False)
    db.add(ThreadsPost(content="Ставка 30,50 зл/год netto для студентів на складі.",
                       content_type="FAQ", language="uk", status=PostStatus.PUBLISHED,
                       published_at=datetime.now(timezone.utc) - timedelta(days=1)))
    await db.commit()

    captured: dict = {}
    monkeypatch.setattr(worker_main, "AIService", _planner_factory(captured))
    monkeypatch.setattr(worker_main, "_auto_schedule_post", _noop)

    await content_plan(db, {"count": 1, "language": "uk"})

    instance = captured.get("instance")
    assert instance is not None, "the daily planner never instantiated the AI service"
    covered = instance.calls[0].get("covered_subjects")
    assert covered == ["PAY"]
