"""Regression tests for the recruitment funnel.

A candidate signal is not a candidate. The conversion used to be one button
press, so the dashboard could show "candidates" that nobody had ever
reached. These tests pin down the rule: a candidate record requires a
confirmed contact, and every stage change is a recorded manual decision.
"""
import pytest
from sqlalchemy import select

from app.enums import CandidateStage, CandidateSignalStatus
from app.models import Candidate, CandidateSignal, CandidateStageEvent
from app.services import candidate_funnel, change_candidate_stage, convert_signal


async def _signal(db, username: str = "candidate_demo") -> CandidateSignal:
    signal = CandidateSignal(
        interaction_id=1, username=username, intent="HOUSING_QUESTION", confidence=0.9,
        status=CandidateSignalStatus.NEW,
    )
    db.add(signal)
    await db.commit()
    return signal


@pytest.mark.asyncio
async def test_conversion_requires_a_confirmed_contact(client, db):
    signal = await _signal(db)
    response = await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={})
    assert response.status_code == 422

    candidate = (await db.execute(select(Candidate))).scalars().first()
    assert candidate is None


@pytest.mark.asyncio
async def test_conversion_records_the_contact_and_the_first_stage(client, db):
    signal = await _signal(db)
    response = await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={
        "channel": "TELEGRAM", "contact_value": "@candidate_demo", "consent_given": True,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["channel"] == "TELEGRAM"
    assert body["contact_value"] == "@candidate_demo"
    assert body["confirmed_at"] is not None
    assert body["consent_given"] is True
    assert body["status"] == CandidateStage.NEW.value

    await db.refresh(signal)
    assert signal.status == CandidateSignalStatus.CONVERTED
    events = list((await db.execute(select(CandidateStageEvent))).scalars())
    assert len(events) == 1
    assert events[0].to_stage == CandidateStage.NEW.value


@pytest.mark.asyncio
async def test_stage_transition_is_manual_and_recorded(client, db):
    signal = await _signal(db)
    candidate = (await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={
        "channel": "THREADS", "contact_value": "@candidate_demo",
    })).json()

    moved = await client.patch(f"/api/candidates/{candidate['id']}/stage", json={"stage": "CONTACTED"})
    assert moved.status_code == 200
    assert moved.json()["status"] == CandidateStage.CONTACTED.value

    events = list((await db.execute(select(CandidateStageEvent).order_by(CandidateStageEvent.id))).scalars())
    assert [event.to_stage for event in events] == ["NEW", "CONTACTED"]
    assert events[-1].from_stage == CandidateStage.NEW.value


@pytest.mark.asyncio
async def test_forbidden_transition_is_rejected(client, db):
    signal = await _signal(db)
    candidate = (await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={
        "channel": "PHONE", "contact_value": "+48 500 000 000",
    })).json()

    # A candidate without a confirmed contact cannot jump straight to hired.
    blocked = await client.patch(f"/api/candidates/{candidate['id']}/stage", json={"stage": "HIRED", "reason": "ok"})
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_hiring_requires_a_reason(client, db):
    signal = await _signal(db)
    candidate = (await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={
        "channel": "PHONE", "contact_value": "+48 500 000 000",
    })).json()
    for stage in ("CONTACTED", "QUALIFIED"):
        moved = await client.patch(f"/api/candidates/{candidate['id']}/stage", json={"stage": stage})
        assert moved.status_code == 200

    without_reason = await client.patch(f"/api/candidates/{candidate['id']}/stage", json={"stage": "HIRED"})
    assert without_reason.status_code == 422
    with_reason = await client.patch(f"/api/candidates/{candidate['id']}/stage", json={"stage": "HIRED", "reason": "Принят на склад"})
    assert with_reason.status_code == 200


@pytest.mark.asyncio
async def test_funnel_counts_the_confirmed_reach_not_the_signals(db):
    signal = await _signal(db)
    await convert_signal(db, signal, channel="TELEGRAM", contact_value="@candidate_demo")

    report = await candidate_funnel(db)

    assert report["signals_total"] == 1
    assert report["signals_converted"] == 1
    assert report["candidates_total"] == 1
    assert report["contacts_confirmed"] == 1
    assert report["stage_hired"] == 0
    # A confirmed contact is not a hire.
    assert report["stage_new"] == 1


@pytest.mark.asyncio
async def test_dashboard_reports_the_confirmed_funnel(client, db):
    signal = await _signal(db)
    await client.post(f"/api/threads/candidate-signals/{signal.id}/convert", json={
        "channel": "THREADS", "contact_value": "@candidate_demo",
    })

    dashboard = (await client.get("/api/dashboard")).json()
    stats = dashboard["stats"]
    funnel = dashboard["funnel"]

    assert funnel["contacts_confirmed"] == 1
    assert funnel["candidates_hired"] == 0
    assert funnel["signals_in_queue"] == 0
    # A raw signal count must not sit next to a hiring number.
    assert "candidate_signals" not in stats
