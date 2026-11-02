import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

import app.api as api
from app.config import settings
from app.models import BackgroundJob, ThreadsAccount
from app.threads_client import ThreadsAPIError, decrypt_token, encrypt_token, make_state


@pytest.mark.asyncio
async def test_oauth_callback_rejects_tampered_state(client, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "oauth-test-key")
    response = await client.get("/api/threads/oauth/callback", params={"code": "code-1", "state": "tampered"})
    assert response.status_code == 302
    assert response.headers["location"].endswith("/account?threads_error=state")


@pytest.mark.asyncio
@pytest.mark.parametrize("debug_available", [True, False])
async def test_oauth_callback_encrypts_token_and_connects_account(client, db, monkeypatch, debug_available):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_app_id", "app-1")
    monkeypatch.setattr(settings, "threads_app_secret", "app-secret")
    monkeypatch.setattr(settings, "threads_token_key", "oauth-test-key")

    async def exchange_code(code, redirect_uri):
        assert code == "code-1"
        assert redirect_uri == settings.threads_redirect_uri
        return {"access_token": "short-token", "user_id": "profile-1"}

    async def exchange_long_lived(*_):
        return {"access_token": "long-token", "expires_in": 3600}

    async def debug_token(*_):
        if not debug_available:
            return None
        return {"data": {"scopes": ["threads_basic", "threads_content_publish"]}}

    class FakeClient:
        def __init__(self, token, profile_id):
            assert token == "long-token"
            assert profile_id == "profile-1"

        async def profile(self):
            return {"id": "profile-1", "username": "recruiter"}

        async def aclose(self):
            return None

    monkeypatch.setattr(api, "exchange_code", exchange_code)
    monkeypatch.setattr(api, "exchange_long_lived", exchange_long_lived)
    monkeypatch.setattr(api, "debug_token", debug_token)
    monkeypatch.setattr(api, "LiveThreadsClient", FakeClient)
    previous = ThreadsAccount(profile_id="previous-profile", username="previous", connected=True)
    db.add(previous)
    await db.commit()

    start = await client.post("/api/threads/account/connect")
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    assert "HttpOnly" in start.headers["set-cookie"]
    response = await client.get("/api/threads/oauth/callback", params={"code": "code-1", "state": state})
    assert response.status_code == 302
    assert response.headers["location"].endswith("/account?threads_connected=1")

    account = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.profile_id == "profile-1"))).scalar_one()
    await db.refresh(previous)
    assert previous.connected is False
    assert account.connected is True
    assert account.is_mock is False
    assert account.access_token_encrypted != "long-token"
    assert decrypt_token(account.access_token_encrypted) == "long-token"
    assert account.permissions == (["threads_basic", "threads_content_publish"] if debug_available else ["threads_basic"])


@pytest.mark.asyncio
async def test_generated_token_import_encrypts_and_connects_account(client, db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "token-import-test-key")

    class FakeClient:
        def __init__(self, token, profile_id=None):
            assert token == "generated-token-that-is-long-enough"
            assert profile_id is None

        async def profile(self):
            return {"id": "profile-token", "username": "saltgroup_artem"}

        async def publishing_limit(self):
            return {"data": [{"quota_usage": 0}]}

        async def aclose(self):
            return None

    async def debug_token(*_):
        return {"data": {"scopes": ["threads_basic", "threads_content_publish"], "expires_at": 2_000_000_000}}

    monkeypatch.setattr(api, "LiveThreadsClient", FakeClient)
    monkeypatch.setattr(api, "debug_token", debug_token)

    response = await client.post("/api/threads/account/token", json={"access_token": "generated-token-that-is-long-enough"})
    assert response.status_code == 200
    assert response.json()["username"] == "saltgroup_artem"
    assert response.json()["connected"] is True

    account = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.profile_id == "profile-token"))).scalar_one()
    assert account.access_token_encrypted != "generated-token-that-is-long-enough"
    assert decrypt_token(account.access_token_encrypted) == "generated-token-that-is-long-enough"
    assert account.permissions == ["threads_basic", "threads_content_publish"]


@pytest.mark.asyncio
async def test_account_probe_persists_meta_app_block_and_reports_affected_schedule(client, db, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_token_key", "blocked-account-test-key")
    account = ThreadsAccount(
        profile_id="blocked-profile",
        username="recruiter",
        access_token_encrypted=encrypt_token("stored-live-token"),
        permissions=["threads_basic", "threads_content_publish"],
        connected=True,
        is_mock=False,
        api_status="READY",
    )
    db.add(account)
    await db.flush()
    from app.enums import ContentType, PostStatus
    from app.models import ThreadsPost
    db.add(ThreadsPost(
        account_id=account.id,
        content="Scheduled post",
        content_type=ContentType.EDUCATIONAL,
        language="uk",
        status=PostStatus.SCHEDULED,
    ))
    await db.commit()

    class BlockedClient:
        async def profile(self):
            raise ThreadsAPIError(
                "Meta blocked API access",
                status_code=400,
                category="app_blocked",
                graph_code=200,
                graph_subcode=2018001,
                fbtrace_id="TRACE-BLOCKED",
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(api, "get_threads_client", lambda *_: BlockedClient())
    response = await client.post("/api/threads/account/test")

    assert response.status_code == 200
    detail = response.json()
    assert detail["api_status"] == "BLOCKED"
    assert detail["last_error_trace_id"] == "TRACE-BLOCKED"

    await db.refresh(account)
    assert account.connected is True
    assert account.api_status == "BLOCKED"
    assert account.last_error_code == 200
    state = (await client.get("/api/threads/account")).json()
    assert state["publishing_ready"] is False
    assert state["scheduled_posts_affected"] == 1


@pytest.mark.asyncio
async def test_webhook_requires_valid_signature_and_queues_one_sync(client, db, monkeypatch):
    monkeypatch.setattr(settings, "threads_app_secret", "webhook-secret")
    payload = {"entry": [{"field": "replies"}]}
    raw = json.dumps(payload, separators=(",", ":")).encode()

    rejected = await client.post(
        "/api/threads/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalid"},
    )
    assert rejected.status_code == 403

    signature = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}"}
    first = await client.post("/api/threads/webhook", content=raw, headers=headers)
    second = await client.post("/api/threads/webhook", content=raw, headers=headers)
    assert first.status_code == second.status_code == 200
    jobs = list((await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "sync_community"))).scalars())
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_webhook_subscription_challenge(client, monkeypatch):
    monkeypatch.setattr(settings, "threads_webhook_verify_token", "verify-me")
    response = await client.get(
        "/api/threads/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "challenge-123"},
    )
    assert response.status_code == 200
    assert response.text == "challenge-123"
