from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.monitoring_status import radar_status
import pytest
from sqlalchemy import select
from app.models import ThreadsAccount, SyncState
from app.config import settings
from worker import main as worker
from app.monitoring_status import public_search_unavailable_reason


def test_search_capability_uses_current_connection_not_saved_success():
    now = datetime.now(timezone.utc)
    account = ThreadsAccount(connected=True, is_mock=False, api_status='READY',
        access_token_encrypted='test', permissions=['threads_keyword_search'])
    assert public_search_unavailable_reason(account, now) is None
    account.permissions = ['threads_basic']
    assert 'threads_keyword_search' in public_search_unavailable_reason(account, now)
    account.permissions = ['threads_keyword_search']
    account.token_expires_at = now - timedelta(seconds=1)
    assert 'истёк' in public_search_unavailable_reason(account, now)
    account.token_expires_at = None
    account.connected = False
    assert public_search_unavailable_reason(account, now)
    account.connected = True
    account.is_mock = True
    assert public_search_unavailable_reason(account, now)


@pytest.mark.asyncio
async def test_expired_search_token_never_decrypted_and_news_still_runs(db, monkeypatch):
    monkeypatch.setattr(settings, 'threads_mock_mode', False)
    db.add(ThreadsAccount(profile_id='expired-search', username='test', connected=True,
        is_mock=False, api_status='READY', access_token_encrypted='test',
        permissions=['threads_keyword_search'],
        token_expires_at=datetime.now(timezone.utc)-timedelta(days=1)))
    await db.flush()
    def forbidden(*args, **kwargs):
        raise AssertionError('Expired token must not be decrypted')
    monkeypatch.setattr(worker, 'decrypt_token', forbidden)
    calls = []
    class NewsClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs):
            calls.append(args)
            return SimpleNamespace(content=b'<rss><channel/></rss>', raise_for_status=lambda: None)
    monkeypatch.setattr(worker.httpx, 'AsyncClient', lambda **kwargs: NewsClient())
    await worker.scout_sources(db)
    state = (await db.execute(select(SyncState).where(SyncState.resource == 'threads_trend_radar'))).scalar_one()
    assert 'истёк' in state.last_error
    assert calls


def test_public_source_freshness_accepts_iso_and_rss_dates():
    now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    assert worker.recent_source_timestamp('2026-10-09T10:00:00Z', now)
    assert worker.recent_source_timestamp('Fri, 09 Oct 2026 10:00:00 GMT', now)
    for value in (None, '', 'invalid', '2026-10-09T10:00:00',
                  '2026-09-01T10:00:00Z', '2026-10-10T10:00:00Z'):
        assert worker.recent_source_timestamp(value, now) is None


@pytest.mark.asyncio
async def test_partial_search_failure_does_not_clear_current_error(db, monkeypatch):
    monkeypatch.setattr(settings, 'threads_mock_mode', False)
    db.add(ThreadsAccount(profile_id='test', username='test', connected=True,
                         is_mock=False, access_token_encrypted='test',
                         permissions=['threads_keyword_search'], api_status='READY'))
    await db.flush()
    class SearchClient:
        calls = 0
        async def keyword_search(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise worker.ThreadsAPIError('Denied', category='auth')
            return []
        async def aclose(self): pass
    class NewsClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs):
            return SimpleNamespace(content=b'<rss><channel/></rss>', raise_for_status=lambda: None)
    monkeypatch.setattr(worker, 'decrypt_token', lambda _: 'test')
    monkeypatch.setattr(worker, 'get_threads_client', lambda *args: SearchClient())
    monkeypatch.setattr(worker.httpx, 'AsyncClient', lambda **kwargs: NewsClient())
    await worker.scout_sources(db)
    state = (await db.execute(select(SyncState).where(SyncState.resource == 'threads_trend_radar'))).scalar_one()
    assert state.last_error == 'Threads keyword search: auth'
    assert radar_status(state) == 'unavailable'


def test_error_overrides_historical_success():
    now = datetime.now(timezone.utc)
    assert radar_status(SimpleNamespace(last_error='auth', last_success_at=now), now) == 'unavailable'


def test_fresh_empty_search_is_available_but_old_success_is_not():
    now = datetime.now(timezone.utc)
    assert radar_status(SimpleNamespace(last_error=None, last_success_at=now), now) == 'available'
    assert radar_status(SimpleNamespace(last_error=None, last_success_at=now-timedelta(days=2)), now) == 'no_recent_data'
    assert radar_status(None, now) == 'no_recent_data'


@pytest.mark.asyncio
async def test_missing_search_scope_does_not_call_threads_or_break_news(db, monkeypatch):
    monkeypatch.setattr(settings, 'threads_mock_mode', False)
    account = ThreadsAccount(profile_id='test', username='test', connected=True, is_mock=False, access_token_encrypted='never-decrypt', permissions=['threads_basic', 'threads_content_publish'], api_status='READY')
    db.add(account)
    await db.flush()
    def forbidden(*args, **kwargs):
        raise AssertionError('Must not call Threads without public-search permission')
    monkeypatch.setattr(worker, 'get_threads_client', forbidden)
    class NewsClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs):
            return SimpleNamespace(content=b'<rss><channel/></rss>', raise_for_status=lambda: None)
    monkeypatch.setattr(worker.httpx, 'AsyncClient', lambda **kwargs: NewsClient())
    await worker.scout_sources(db)
    state = (await db.execute(select(SyncState).where(SyncState.resource == 'threads_trend_radar'))).scalar_one()
    assert 'threads_keyword_search' in state.last_error
    assert radar_status(state) == 'unavailable'
    await db.refresh(account)
    assert account.api_status == 'READY'
