import logging
from unittest.mock import AsyncMock

import pytest
from app.config import settings
from app.logging import configure_logging
from app.threads_client import scopes_from_debug, make_state, validate_state


@pytest.mark.parametrize("payload,expected", [
    ({"data": {"scopes": ["threads_basic", "threads_read_replies"]}}, ["threads_basic", "threads_read_replies"]),
    ({"data": {"granted_scopes": ["threads_basic"]}}, ["threads_basic"]),
    ({"data": {"scopes": [], "granted_scopes": ["threads_content_publish"]}}, []),
    (None, []),
    ({"data": {"scopes": "threads_basic"}}, []),
    ({"data": {"is_valid": False, "scopes": ["threads_content_publish"]}}, []),
])
def test_actual_permission_payload(payload, expected):
    assert scopes_from_debug(payload) == expected


def test_future_state_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "threads_token_key", "test-state-key")
    import app.threads_client as threads
    now = threads.time.time()
    monkeypatch.setattr(threads.time, "time", lambda: now + 3600)
    state = make_state()
    monkeypatch.setattr(threads.time, "time", lambda: now)
    assert not validate_state(state)


@pytest.mark.asyncio
async def test_valid_state_without_originating_browser_is_rejected(client, monkeypatch):
    import app.api as api
    monkeypatch.setattr(settings, "threads_token_key", "test-state-key")
    exchange = AsyncMock()
    monkeypatch.setattr(api, "exchange_code", exchange)
    result = await client.get("/api/threads/oauth/callback", params={"state": make_state(), "code": "untrusted-code"})
    assert result.headers["location"].endswith("threads_error=state")
    exchange.assert_not_awaited()


def test_http_libraries_do_not_log_oauth_query_secrets():
    configure_logging()
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.DEBUG)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], None, "unexpected"])
async def test_non_object_api_json_becomes_normalized_error(payload):
    import httpx
    from app.threads_client import LiveThreadsClient, ThreadsAPIError
    client = LiveThreadsClient("mock-test-token", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)))
    try:
        with pytest.raises(ThreadsAPIError) as error:
            await client.profile()
        assert error.value.category == "api"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_debug_token_authenticates_before_reading_actual_scopes(monkeypatch):
    import httpx
    import app.threads_client as threads
    real_client = httpx.AsyncClient
    scopes = ["threads_basic", "threads_read_replies", "threads_manage_insights"]

    def handler(request):
        assert request.url.path == "/debug_token"
        assert request.headers["Authorization"] == "Bearer fake-inspected-token"
        assert request.url.params["input_token"] == "fake-inspected-token"
        return httpx.Response(200, json={"data": {"is_valid": True, "scopes": scopes}})

    monkeypatch.setattr(settings, "threads_app_id", "test-app")
    monkeypatch.setattr(threads.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))
    assert await threads.granted_scopes("fake-inspected-token") == scopes
