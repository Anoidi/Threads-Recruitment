from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.threads_client import (
    LiveThreadsClient, MockThreadsClient, ThreadsAPIError, _classify_error,
    decrypt_token, encrypt_token, make_state, validate_state,
)


@pytest.mark.asyncio
async def test_mock_threads_client_has_deterministic_publish_id():
    client = MockThreadsClient()
    first = await client.publish_text("hello", "key-1")
    second = await client.publish_text("hello", "key-1")
    assert first["id"] == second["id"] == "mock-key-1"


def test_live_client_refuses_missing_token():
    with pytest.raises(ThreadsAPIError):
        LiveThreadsClient("")


def test_tokens_are_encrypted_and_oauth_state_is_short_lived(monkeypatch):
    monkeypatch.setattr(settings, "threads_token_key", "unit-test-token-key")
    encrypted = encrypt_token("secret-access-token")
    assert encrypted != "secret-access-token"
    assert "secret-access-token" not in encrypted
    assert decrypt_token(encrypted) == "secret-access-token"
    state = make_state()
    assert validate_state(state)
    assert not validate_state(state + "tampered")


@pytest.mark.asyncio
async def test_real_client_retries_transient_5xx_without_leaking_token(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert "access_token" not in str(request.url)
        assert request.headers["Authorization"] == "Bearer test-token"
        if attempts == 1:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json={"id": "42", "username": "recruiter"})

    monkeypatch.setattr(settings, "threads_max_retries", 1)
    client = LiveThreadsClient("test-token", transport=httpx.MockTransport(handler))
    with patch("app.threads_client.asyncio.sleep", new=AsyncMock()):
        assert (await client.profile())["id"] == "42"
    assert attempts == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_real_client_classifies_429_and_honors_bounded_retry(monkeypatch):
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": {"message": "Rate limit reached"}})

    monkeypatch.setattr(settings, "threads_max_retries", 1)
    client = LiveThreadsClient("test-token", transport=httpx.MockTransport(handler))
    with patch("app.threads_client.asyncio.sleep", new=AsyncMock()) as sleeper:
        with pytest.raises(ThreadsAPIError) as caught:
            await client.profile()
    assert caught.value.category == "rate_limit"
    assert caught.value.retryable is True
    assert attempts == 2
    sleeper.assert_awaited_once()
    await client.aclose()


@pytest.mark.asyncio
async def test_real_client_preserves_meta_block_metadata_and_does_not_retry(monkeypatch):
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": {
            "message": "API access blocked.",
            "type": "OAuthException",
            "code": 200,
            "error_subcode": 2018001,
            "is_transient": False,
            "fbtrace_id": "TRACE-123",
        }})

    monkeypatch.setattr(settings, "threads_max_retries", 3)
    client = LiveThreadsClient("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(ThreadsAPIError) as caught:
        await client.profile()

    error = caught.value
    assert error.category == "app_blocked"
    assert error.retryable is False
    assert error.graph_code == 200
    assert error.graph_subcode == 2018001
    assert error.graph_type == "OAuthException"
    assert error.fbtrace_id == "TRACE-123"
    assert attempts == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_real_two_step_publish_returns_remote_id(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/threads_publishing_limit"):
            return httpx.Response(200, json={"quota_usage": 0, "config": [{"quota_total": 250}]})
        if request.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path.endswith("/container-1"):
            return httpx.Response(200, json={"status": "FINISHED"})
        if request.url.path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "remote-post-1"})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    monkeypatch.setattr(settings, "threads_max_retries", 0)
    client = LiveThreadsClient("test-token", "user-1", transport=httpx.MockTransport(handler))
    result = await client.publish_text("Approved factual post", "local-idempotency-key")
    assert result["id"] == "remote-post-1"
    assert calls[-1].endswith("/threads_publish")
    await client.aclose()


@pytest.mark.asyncio
async def test_publish_missing_remote_id_is_marked_as_unknown_outcome(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/threads_publishing_limit"):
            return httpx.Response(200, json={"quota_usage": 0, "config": [{"quota_total": 250}]})
        if request.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path.endswith("/container-1"):
            return httpx.Response(200, json={"status": "FINISHED"})
        if request.url.path.endswith("/threads_publish"):
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    monkeypatch.setattr(settings, "threads_max_retries", 0)
    client = LiveThreadsClient("test-token", "user-1", transport=httpx.MockTransport(handler))
    with pytest.raises(ThreadsAPIError) as caught:
        await client.publish_text("Approved factual post", "local-idempotency-key")
    assert caught.value.category == "unknown_outcome"
    assert caught.value.duplicate_risk is True
    assert caught.value.retryable is False
    await client.aclose()


@pytest.mark.asyncio
async def test_reply_scan_never_passes_the_sync_cursor_to_the_post_list():
    """Meta filters `since` by post publication time, not by reply time.

    Passing the sync cursor into the post list hid every reply that arrived
    more than an hour after its post was published. For eleven days that made
    the inbox look empty while Threads held dozens of real comments.
    """
    post_requests: list[httpx.URL] = []
    old_post = {"id": "post-1", "timestamp": "2026-10-12T12:08:00+0000", "has_replies": True, "text": "Vacancy"}
    recent_reply = {
        "id": "reply-1", "username": "candidate", "text": "Чи є житло?",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "replied_to": {"id": "post-1"}, "root_post": {"id": "post-1"},
        "is_reply_owned_by_me": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "user-1", "username": "recruiter"})
        if request.url.path.endswith("/threads"):
            post_requests.append(request.url)
            since = request.url.params.get("since")
            # Reproduce Meta: `since` filters the post list by publication time.
            data = [] if since else [old_post]
            return httpx.Response(200, json={"data": data})
        if request.url.path.endswith("/conversation"):
            return httpx.Response(200, json={"data": [recent_reply]})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    client = LiveThreadsClient("test-token", "user-1", transport=httpx.MockTransport(handler))
    # A cursor that is one hour behind the last successful sync.
    replies = await client.replies(datetime.now(timezone.utc) - timedelta(hours=1))
    await client.aclose()

    assert post_requests, "the post list must be requested"
    assert all("since" not in url.params for url in post_requests)
    assert [item["id"] for item in replies] == ["reply-1"]


@pytest.mark.asyncio
async def test_reply_scan_counts_what_the_cursor_hides():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "user-1", "username": "recruiter"})
        if request.url.path.endswith("/threads"):
            return httpx.Response(200, json={"data": [
                {"id": "post-1", "timestamp": "2026-10-12T12:08:00+0000", "has_replies": True, "text": "Vacancy"},
            ]})
        if request.url.path.endswith("/conversation"):
            return httpx.Response(200, json={"data": [
                {"id": "old-reply", "username": "candidate", "text": "старое", "timestamp": "2026-10-12T13:00:00+0000",
                 "replied_to": {"id": "post-1"}, "root_post": {"id": "post-1"}, "is_reply_owned_by_me": False},
            ]})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    client = LiveThreadsClient("test-token", "user-1", transport=httpx.MockTransport(handler))
    replies = await client.replies(datetime.now(timezone.utc))
    await client.aclose()

    assert replies == []
    assert client.cursor_dropped == 1


def test_unloadable_object_is_a_permanent_condition_not_a_retry():
    error = _classify_error(400, {"error": {
        "message": "Unsupported get request. Object with ID '18010158951018935' does not exist, "
                   "cannot be loaded due to missing permissions, or does not support this operation",
    }})
    assert error.category == "not_owned"
    assert error.retryable is False
    assert error.deferred is False


@pytest.mark.asyncio
async def test_publishing_quota_is_read_from_the_live_response_shape(monkeypatch):
    """The live endpoint wraps the quota in `data` and uses an object config.

    Both shapes were misread, so the publishing quota was never enforced.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/threads_publishing_limit"):
            return httpx.Response(200, json={"data": [{
                "quota_usage": 250,
                "config": {"quota_total": 250, "quota_duration": 86400},
                "reply_quota_usage": 0,
                "reply_config": {"quota_total": 1000, "quota_duration": 86400},
            }]})
        if request.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path.endswith("/container-1"):
            return httpx.Response(200, json={"status": "FINISHED"})
        if request.url.path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "remote-post-1"})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    monkeypatch.setattr(settings, "threads_max_retries", 0)
    client = LiveThreadsClient("test-token", "user-1", transport=httpx.MockTransport(handler))
    limit = await client.publishing_limit()
    assert limit["quota_usage"] == 250
    with pytest.raises(ThreadsAPIError) as caught:
        await client.publish_text("Approved factual post", "quota-key")
    assert caught.value.category == "rate_limit"
    await client.aclose()

