import asyncio
import pytest
from worker import main as worker


@pytest.mark.asyncio
async def test_worker_recovers_claim_failure_without_busy_loop(monkeypatch):
    calls = []
    delays = []
    monkeypatch.setattr(worker, 'monotonic', lambda: 0)
    async def claim():
        calls.append('claim')
        if len(calls) == 1:
            raise ConnectionError('temporary database outage')
        return None
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError()
    monkeypatch.setattr(worker, 'claim_job', claim)
    monkeypatch.setattr(worker.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert calls == ['claim', 'claim']
    assert delays == [5, 2]


@pytest.mark.asyncio
async def test_worker_cancellation_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(worker, 'monotonic', lambda: 0)
    async def claim():
        raise asyncio.CancelledError()
    monkeypatch.setattr(worker, 'claim_job', claim)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()


@pytest.mark.asyncio
async def test_failed_processing_is_not_immediately_replayed(monkeypatch):
    monkeypatch.setattr(worker, 'monotonic', lambda: 0)
    claimed = iter([42, None])
    processed = []
    delays = []
    async def claim(): return next(claimed)
    async def process(job_id):
        processed.append(job_id)
        raise ConnectionError('failure after external operation')
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError()
    monkeypatch.setattr(worker, 'claim_job', claim)
    monkeypatch.setattr(worker, 'process_job', process)
    monkeypatch.setattr(worker.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever()
    assert processed == [42]
    assert delays == [5, 2]


@pytest.mark.asyncio
async def test_long_async_job_keeps_heartbeat_alive(monkeypatch):
    beats = []
    async def process(job_id):
        await asyncio.sleep(0.04)
    async def heartbeat(): beats.append('alive')
    monkeypatch.setattr(worker, 'process_job', process)
    monkeypatch.setattr(worker, 'heartbeat', heartbeat)
    await worker.process_with_heartbeat(42, interval=0.01)
    assert len(beats) >= 2


@pytest.mark.asyncio
async def test_wait_for_wakeup_consumes_role_specific_redis_queue(monkeypatch):
    from app.config import settings
    from app.queue import WAKEUP_KEY_AGENTS, WAKEUP_KEY_PRIMARY, wakeup_key_for_kind

    assert wakeup_key_for_kind("publish_post") == WAKEUP_KEY_PRIMARY
    assert wakeup_key_for_kind("run_agent") == WAKEUP_KEY_AGENTS
    assert wakeup_key_for_kind("content_plan") == WAKEUP_KEY_AGENTS

    popped = []
    closed = []

    class FakeRedis:
        async def blpop(self, key, timeout=0):
            popped.append((key, timeout))
            return (key, "99")

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(worker.Redis, "from_url", lambda *a, **kw: FakeRedis())
    monkeypatch.setattr(settings, "worker_role", "primary")
    assert await worker.wait_for_wakeup(2) is True
    assert popped == [(WAKEUP_KEY_PRIMARY, 2)]
    assert closed == [True]

    popped.clear()
    monkeypatch.setattr(settings, "worker_role", "agents")
    assert await worker.wait_for_wakeup(2) is True
    assert popped == [(WAKEUP_KEY_AGENTS, 2)]


@pytest.mark.asyncio
async def test_wait_for_wakeup_falls_back_to_sleep_when_redis_fails(monkeypatch):
    slept = []

    class BrokenRedis:
        async def blpop(self, key, timeout=0):
            raise ConnectionError("redis down")

        async def aclose(self):
            pass

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(worker.Redis, "from_url", lambda *a, **kw: BrokenRedis())
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(worker, "_REAL_ASYNCIO_SLEEP", fake_sleep)
    assert await worker.wait_for_wakeup(2) is False
    assert slept == [2]
