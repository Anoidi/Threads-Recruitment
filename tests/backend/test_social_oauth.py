from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

import app.api as api
from app.config import settings
from app.enums import SocialPlatform
from app.social_clients import SocialToken


@pytest.mark.asyncio
async def test_social_accounts_report_unconfigured_providers(client, monkeypatch):
    monkeypatch.setattr(settings, "instagram_app_id", "")
    monkeypatch.setattr(settings, "instagram_app_secret", "")
    monkeypatch.setattr(settings, "tiktok_client_key", "")
    monkeypatch.setattr(settings, "tiktok_client_secret", "")
    response = await client.get("/api/social/accounts")
    assert response.status_code == 200
    assert {item["platform"] for item in response.json()} == {"INSTAGRAM", "TIKTOK"}
    assert all(item["token_status"] == "CONFIG_ERROR" for item in response.json())


@pytest.mark.asyncio
async def test_social_oauth_state_is_single_use_and_token_is_encrypted(client, db, monkeypatch):
    monkeypatch.setattr(settings, "social_token_key", "social-oauth-test-key")
    monkeypatch.setattr(settings, "instagram_app_id", "instagram-app")
    monkeypatch.setattr(settings, "instagram_app_secret", "instagram-secret")
    monkeypatch.setattr(settings, "instagram_redirect_uri", "http://localhost:8000/api/social/oauth/instagram/callback")

    started = await client.post("/api/social/INSTAGRAM/oauth/start")
    assert started.status_code == 200
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]

    async def fake_exchange(platform, code):
        assert platform == SocialPlatform.INSTAGRAM
        assert code == "one-time-code"
        return SocialToken("raw-instagram-token", None, "ig-1", "recruiter", ["instagram_business_basic"], datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(api, "exchange_social_code", fake_exchange)
    callback = await client.get("/api/social/oauth/INSTAGRAM/callback", params={"code": "one-time-code", "state": state}, follow_redirects=False)
    assert callback.status_code == 302
    assert "social_connected=instagram" in callback.headers["location"]

    # Replaying the same callback state is rejected and the stored value is not
    # the raw token.
    replay = await client.get("/api/social/oauth/INSTAGRAM/callback", params={"code": "one-time-code", "state": state}, follow_redirects=False)
    assert "social_error=instagram:state" in replay.headers["location"]
    accounts = await client.get("/api/social/accounts")
    assert accounts.json()[0]["connected"] is True
    assert accounts.json()[0]["token_status"] == "VALID"
