from app.models import ThreadsAccount
import pytest
from app.threads_client import ThreadsAPIError
from app.threads_status import mark_threads_error, mark_threads_ready, threads_publishing_ready


def test_expired_token_overrides_historical_ready_status():
    from datetime import datetime, timedelta, timezone
    account = ThreadsAccount(connected=True, is_mock=False, api_status='READY',
        permissions=['threads_content_publish'], token_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    assert not threads_publishing_ready(account)
    account.token_expires_at = account.token_expires_at.replace(tzinfo=None)
    assert not threads_publishing_ready(account)


@pytest.mark.asyncio
async def test_worker_rejects_expired_ready_token_before_external_job(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from worker import main as worker
    from app.config import settings
    monkeypatch.setattr(settings, 'threads_mock_mode', False)
    expired = ThreadsAccount(connected=True, is_mock=False, api_status='READY',
        token_expires_at=datetime.now(timezone.utc)-timedelta(days=1))
    async def account(_): return expired
    monkeypatch.setattr(worker, '_connected_account', account)
    with pytest.raises(ThreadsAPIError, match='token expired') as failure:
        await worker._raise_if_threads_blocked(None, 'publish_post')
    assert failure.value.category == 'auth'


def test_meta_application_block_is_persisted_separately_from_token_connection():
    account = ThreadsAccount(
        profile_id="profile-1",
        username="recruiter",
        connected=True,
        is_mock=False,
        permissions=["threads_basic", "threads_content_publish"],
    )
    error = ThreadsAPIError(
        "Meta blocked API access",
        status_code=400,
        category="app_blocked",
        graph_code=200,
        graph_subcode=2018001,
        fbtrace_id="TRACE-123",
    )

    mark_threads_error(account, error)

    assert account.connected is True
    assert account.api_status == "BLOCKED"
    assert account.last_error_code == 200
    assert account.last_error_subcode == 2018001
    assert account.last_error_trace_id == "TRACE-123"
    assert account.blocked_at is not None
    assert threads_publishing_ready(account) is False


def test_successful_probe_clears_block_and_restores_publishing_readiness():
    account = ThreadsAccount(
        profile_id="profile-1",
        username="recruiter",
        connected=True,
        is_mock=False,
        permissions=["threads_basic", "threads_content_publish"],
        api_status="BLOCKED",
        last_error="API access blocked",
        last_error_code=200,
        last_error_trace_id="TRACE-123",
    )

    mark_threads_ready(account)

    assert account.api_status == "READY"
    assert account.last_error is None
    assert account.last_error_code is None
    assert account.last_error_trace_id is None
    assert threads_publishing_ready(account) is True
