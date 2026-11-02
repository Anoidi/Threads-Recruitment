"""P0 measurement guarantees.

The system measures recruitment, not reach. These tests pin down the rules
that make a content comparison honest:

* results are compared inside equal observation windows (24/48/72 h);
* a post without a closed window is not part of that window's comparison;
* one person is one candidate per post, even after several comments;
* a signal arriving after the window does not count inside it;
* an ignored signal is not interest;
* views alone never produce a winner;
* reading the report never mutates the running experiment.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.experiments import (
    EXPERIMENT_ROUNDS, FORMATS, experiment_needs_round, experiment_report,
    slot_rotation, start_experiment,
)
from app.models import (
    Candidate, CandidateSignal, PostMetric, ThreadExperiment,
    ThreadExperimentAssignment, ThreadsInteraction, ThreadsPost, Vacancy,
)
from app.services import convert_signal


def _now():
    return datetime.now(timezone.utc)


def _vacancy() -> Vacancy:
    return Vacancy(
        title="Magazynier", description="Praca przy pakowaniu", location="Kraków",
        salary="30 PLN/hour", schedule="2 zmiany", language="uk", active=True,
    )


async def _experiment(db, vacancy: Vacancy) -> ThreadExperiment:
    experiment = ThreadExperiment(
        name="Форматы: Ставка", hypothesis="Формат меняет отклик.",
        subject="PAY", fact="30 PLN/hour", vacancy_id=vacancy.id,
        metric="CANDIDATES", status="RUNNING", started_at=_now(),
        variants=[{"key": fmt, "format": fmt, "label": fmt} for fmt in FORMATS],
        created_by="test",
    )
    db.add(experiment)
    await db.flush()
    return experiment


async def _post(db, experiment: ThreadExperiment, variant: str, published_at, *, verified: bool = True) -> ThreadsPost:
    post = ThreadsPost(
        vacancy_id=experiment.vacancy_id,
        content=f"Ставка: {experiment.fact}. Уточніть деталі у роботодавця.",
        hook="Ставка", content_type="FAQ", language="uk",
        status="PUBLISHED", remote_id=f"remote-{uuid4().hex[:12]}",
        published_at=published_at, content_subject="PAY",
    )
    db.add(post)
    await db.flush()
    db.add(ThreadExperimentAssignment(
        experiment_id=experiment.id, variant=variant, format=variant,
        post_id=post.id, fact=experiment.fact, verified=verified,
        mismatch_reason=None if verified else "format",
    ))
    await db.flush()
    return post


async def _snapshot(db, post: ThreadsPost, hours: float, views: int) -> None:
    db.add(PostMetric(
        post_id=post.id, views=views, likes=0, replies=0, reposts=0, quotes=0,
        captured_at=post.published_at + timedelta(hours=hours),
    ))


async def _signal(db, post: ThreadsPost, username: str, *, arrived_hours: float = 2.0,
                  candidate: bool = True, stage: str = "NEW", ignored_signal: bool = False,
                  ignored_interaction: bool = False, reuse: Candidate | None = None) -> Candidate | None:
    interaction = ThreadsInteraction(
        post_id=post.id, remote_id=f"i-{uuid4().hex[:12]}", kind="REPLY",
        username=username, message="Скільки платять?",
        occurred_at=post.published_at + timedelta(hours=arrived_hours),
        ignored=ignored_interaction,
    )
    db.add(interaction)
    await db.flush()
    signal = CandidateSignal(
        interaction_id=interaction.id, vacancy_id=post.vacancy_id,
        username=username, intent="SALARY_QUESTION", confidence=0.9,
        status="IGNORED" if ignored_signal else "NEW",
    )
    db.add(signal)
    await db.flush()
    if not candidate:
        return None
    row = reuse or Candidate(
        name=username, source_signal_id=signal.id, threads_username=username,
        status=stage, confirmed_at=_now() if stage != "NEW" else None,
    )
    if reuse is None:
        db.add(row)
        await db.flush()
    signal.candidate_id = row.id
    await db.flush()
    return row


async def _two_posts_per_variant(db, experiment, base):
    posts = {}
    for variant in FORMATS:
        posts[variant] = [
            await _post(db, experiment, variant, base),
            await _post(db, experiment, variant, base),
        ]
    await db.commit()
    return posts


@pytest.mark.asyncio
async def test_each_window_uses_the_snapshot_taken_at_its_own_end(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=100)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 1, 10)
            await _snapshot(db, post, 24.5, 100)
            await _snapshot(db, post, 50, 200)
            await _snapshot(db, post, 72.5, 300)
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["primary_window"] == 72
    by_hours = {item["hours"]: item for item in report["windows"]}
    assert all(item["complete"] for item in report["windows"])
    assert by_hours[24]["variants"][0]["views_median"] == 100
    assert by_hours[48]["variants"][0]["views_median"] == 200
    assert by_hours[72]["variants"][0]["views_median"] == 300
    assert by_hours[24]["variants"][0]["posts"] == 2
    # The top-level variant figures follow the primary (widest complete) window.
    assert report["variants"][0]["median_views"] == 300
    assert report["variants"][0]["window_posts"] == 2


@pytest.mark.asyncio
async def test_an_open_window_is_not_compared(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=10)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 1, 10)
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["primary_window"] is None
    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert "окно наблюдения" in report["verdict_text"].lower()


@pytest.mark.asyncio
async def test_a_snapshot_outside_the_tolerance_does_not_close_the_window(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=100)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            # 55 hours closes the 48-hour window (tolerance 12) and is much too
            # late for the 24-hour window (tolerance 6).
            await _snapshot(db, post, 55, 200)
    await db.commit()

    report = await experiment_report(db, experiment)
    by_hours = {item["hours"]: item for item in report["windows"]}
    assert by_hours[24]["variants"][0]["posts"] == 0
    assert by_hours[48]["variants"][0]["posts"] == 2
    assert by_hours[72]["variants"][0]["posts"] == 0
    assert report["primary_window"] == 48


@pytest.mark.asyncio
async def test_a_clear_candidate_lead_is_a_result(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    # Two candidates for QUESTION, none for the others.
    await _signal(db, posts["QUESTION"][0], "u1")
    await _signal(db, posts["QUESTION"][0], "u2")
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["verdict"] == "RESULT"
    assert report["winner"]["key"] == "QUESTION"
    assert report["winner"]["candidates"] == 2
    assert report["winner"]["candidates_per_post"] == 1.0


@pytest.mark.asyncio
async def test_a_small_difference_between_formats_is_not_a_winner(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    await _signal(db, posts["FACT"][0], "f1")
    await _signal(db, posts["FACT"][1], "f2")
    await _signal(db, posts["QUESTION"][0], "q1")
    await _signal(db, posts["QUESTION"][1], "q2")
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["verdict"] == "DIFFERENCE_NOT_CLEAR"
    assert report["winner"] is None
    assert "Победитель не объявляется" in report["verdict_text"]


@pytest.mark.asyncio
async def test_views_alone_do_not_produce_a_winner(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variant, variants in posts.items():
        for post in variants:
            await _snapshot(db, post, 24.5, 50_000 if variant == "FACT" else 20)
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["verdict"] == "NO_CANDIDATE_SIGNAL"
    assert report["winner"] is None
    assert report["variants"][0]["median_views"] == 50_000


@pytest.mark.asyncio
async def test_a_person_is_counted_once_per_post(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    first = await _signal(db, posts["QUESTION"][0], "same_person")
    await _signal(db, posts["QUESTION"][0], "same_person", reuse=first)
    await db.commit()

    report = await experiment_report(db, experiment)
    row = next(item for item in report["variants"] if item["key"] == "QUESTION")
    assert row["signals"] == 2
    assert row["candidates"] == 1
    assert row["unique_candidates"] == 1


@pytest.mark.asyncio
async def test_a_person_seen_in_two_variants_is_reported_as_shared(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    first = await _signal(db, posts["FACT"][0], "shared_person")
    await _signal(db, posts["QUESTION"][0], "shared_person", reuse=first)
    await db.commit()

    report = await experiment_report(db, experiment)
    assert report["statistics"]["shared_candidates"] == 1
    assert any("более чем в одном варианте" in item for item in report["caveats"])


@pytest.mark.asyncio
async def test_an_ignored_signal_is_not_interest(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    await _signal(db, posts["FACT"][0], "spam", ignored_signal=True)
    await _signal(db, posts["FACT"][1], "noise", ignored_interaction=True)
    await db.commit()

    report = await experiment_report(db, experiment)
    row = next(item for item in report["variants"] if item["key"] == "FACT")
    assert row["signals"] == 0
    assert row["candidates"] == 0


@pytest.mark.asyncio
async def test_a_signal_after_the_window_does_not_count_inside_it(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    await _signal(db, posts["QUESTION"][0], "early", arrived_hours=2)
    await _signal(db, posts["QUESTION"][0], "late", arrived_hours=28)
    await db.commit()

    report = await experiment_report(db, experiment)
    row = next(item for item in report["variants"] if item["key"] == "QUESTION")
    window_row = next(
        item for item in report["windows"][0]["variants"] if item["key"] == "QUESTION"
    )
    assert row["signals"] == 2  # lifetime
    assert window_row["signals"] == 1  # inside the 24-hour window


@pytest.mark.asyncio
async def test_reading_the_report_never_changes_the_experiment(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    posts = await _two_posts_per_variant(db, experiment, base)
    for variants in posts.values():
        for post in variants:
            await _snapshot(db, post, 24.5, 100)
    await db.commit()

    before = experiment.variants
    await experiment_report(db, experiment)
    await experiment_report(db, experiment)
    await db.refresh(experiment)
    assert experiment.status == "RUNNING"
    assert experiment.report is None
    assert experiment.variants == before


def test_slot_rotation_changes_which_format_gets_the_earliest_slot():
    first = slot_rotation(7, 0, 3)
    second = slot_rotation(7, 1, 3)
    assert sorted(first.values()) == [0, 1, 2]
    assert sorted(second.values()) == [0, 1, 2]
    assert first != second
    # A different experiment must not start with the same format in front.
    assert sorted(slot_rotation(8, 0, 3).values()) == [0, 1, 2]
    assert slot_rotation(7, 0, 3) != slot_rotation(8, 0, 3)


@pytest.mark.asyncio
async def test_an_excluded_post_does_not_consume_a_round(db):
    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    experiment = await _experiment(db, vacancy)
    base = _now() - timedelta(hours=30)
    for variant in FORMATS:
        await _post(db, experiment, variant, base, verified=True)
        await _post(db, experiment, variant, base, verified=False)
    await db.commit()

    # The old rule counted the excluded post and "finished" the experiment with
    # one comparable post per variant, which compares nothing.
    assert await experiment_needs_round(db, experiment) is True
    for variant in FORMATS:
        for _ in range(EXPERIMENT_ROUNDS - 1):
            await _post(db, experiment, variant, base, verified=True)
    await db.commit()
    assert await experiment_needs_round(db, experiment) is False


@pytest.mark.asyncio
async def test_converting_a_second_signal_from_the_same_person_reuses_the_candidate(db, client):
    from app.models import Vacancy as VacancyModel

    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    first_post = ThreadsPost(
        vacancy_id=vacancy.id, content="Ставка 30 PLN/hour.", content_type="FAQ",
        language="uk", status="PUBLISHED", remote_id="p-1", published_at=_now(),
    )
    second_post = ThreadsPost(
        vacancy_id=vacancy.id, content="Ставка 30 PLN/hour, зміни.", content_type="FAQ",
        language="uk", status="PUBLISHED", remote_id="p-2", published_at=_now(),
    )
    db.add_all([first_post, second_post])
    await db.flush()
    first_signal = None
    for index, post in enumerate((first_post, second_post), 1):
        interaction = ThreadsInteraction(
            post_id=post.id, remote_id=f"conv-{index}", kind="REPLY",
            username="candidate_one", message="Хочу працювати",
        )
        db.add(interaction)
        await db.flush()
        signal = CandidateSignal(
            interaction_id=interaction.id, vacancy_id=post.vacancy_id,
            username="candidate_one", intent="JOB_SEARCH", confidence=0.9,
        )
        db.add(signal)
        await db.flush()
        if first_signal is None:
            first_signal = signal
        else:
            second_signal = signal
    await db.commit()

    first = await convert_signal(db, first_signal, channel="THREADS", contact_value="@candidate_one")
    second = await convert_signal(db, second_signal, channel="THREADS", contact_value="@candidate_one")
    assert first.id == second.id
    candidates = list((await db.execute(select(Candidate))).scalars())
    assert len(candidates) == 1
    await db.refresh(second_signal)
    assert second_signal.candidate_id == first.id


@pytest.mark.asyncio
async def test_attribution_quality_flags_a_signal_without_a_post(db):
    from app.recruitment_intelligence import attribution_quality

    interaction = ThreadsInteraction(
        remote_id="orphan", kind="REPLY", username="nobody", message="?", post_id=None,
    )
    db.add(interaction)
    await db.flush()
    db.add(CandidateSignal(
        interaction_id=interaction.id, username="nobody", intent="JOB_SEARCH", confidence=0.5,
    ))
    await db.commit()

    quality = await attribution_quality(db)
    assert quality["signals_total"] == 1
    assert quality["signals_without_post"] == 1
    assert quality["posts_with_signals"] == 0
    assert quality["identity"]


@pytest.mark.asyncio
async def test_attribution_quality_counts_duplicate_candidate_records(db):
    from app.recruitment_intelligence import attribution_quality

    db.add_all([
        Candidate(name="A", threads_username="dup_person"),
        Candidate(name="B", threads_username="dup_person"),
    ])
    await db.commit()

    quality = await attribution_quality(db)
    assert quality["duplicate_handles"] == 1
    assert quality["duplicate_candidate_records"] == 1


@pytest.mark.asyncio
async def test_the_vacancy_funnel_separates_signals_from_candidates(db):
    from app.recruitment_intelligence import vacancy_funnel

    vacancy = _vacancy()
    db.add(vacancy)
    await db.flush()
    post = ThreadsPost(
        vacancy_id=vacancy.id, content="Ставка 30 PLN/hour.", content_type="FAQ",
        language="uk", status="PUBLISHED", remote_id="funnel-1", published_at=_now(),
    )
    db.add(post)
    await db.flush()
    candidate = await _signal(db, post, "qualified_person", stage="QUALIFIED")
    await _signal(db, post, "interested_person", candidate=False)
    await db.commit()

    rows = await vacancy_funnel(db, vacancy_id=vacancy.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["published_posts"] == 1
    assert row["signals"] == 2
    assert row["unique_people"] == 2
    assert row["candidates"] == 1
    assert row["qualified"] == 1
    assert row["contacted"] == 1
    assert row["hired"] == 0
    # The CRM has no interview stage; that is reported as unknown, not as zero.
    assert row["invited"] is None
    assert candidate is not None


@pytest.mark.asyncio
async def test_the_attribution_endpoint_reports_the_funnel(db, client):
    vacancy = (await client.post("/api/vacancies", json={
        "title": "Magazynier", "description": "Pakowanie", "location": "Kraków",
        "salary": "30 PLN/hour", "language": "uk",
    })).json()
    post = (await client.post("/api/threads/posts", json={
        "content": "Ставка 30 PLN/hour для складу.", "vacancy_id": vacancy["id"],
        "content_type": "FAQ", "language": "uk",
    })).json()
    await db.commit()

    response = await client.get(f"/api/threads/attribution?vacancy_id={vacancy['id']}")
    assert response.status_code == 200
    body = response.json()
    assert "quality" in body and "vacancies" in body and "posts" in body
    assert body["quality"]["identity"]
    assert all("invited" in row for row in body["vacancies"])
    assert post["id"] is not None


@pytest.mark.asyncio
async def test_the_analytics_endpoint_uses_the_shared_attribution(db, client):
    response = await client.get("/api/threads/analytics")
    assert response.status_code == 200
    body = response.json()
    assert "editorial_analysis" in body
