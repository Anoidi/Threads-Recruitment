"""Operator-token auth for the whole /api router (audit phase 1.1, D1/D10/D15).

The shared ``client`` fixture in ``conftest.py`` sends the token for every
existing test. The tests below build their own clients *without* the header
to assert the protection, plus a token-carrying client for the happy path.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db
from app.main import create_app


def _build_client(db, with_token: bool) -> AsyncClient:
    """Same override pattern as the shared ``client`` fixture: every request
    reuses the test session, so the code under test reads committed rows."""
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    headers = {"X-Operator-Token": "test-operator-token"} if with_token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testclient", headers=headers)


@pytest_asyncio.fixture
async def open_client(db):
    async with _build_client(db, with_token=False) as http:
        yield http


@pytest_asyncio.fixture
async def auth_client(db):
    async with _build_client(db, with_token=True) as http:
        yield http


@pytest.mark.asyncio
async def test_protected_route_rejects_missing_token(open_client):
    response = await open_client.get("/api/dashboard")
    assert response.status_code == 401
    assert "operator token" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_protected_route_rejects_wrong_token(open_client):
    response = await open_client.get("/api/dashboard", headers={"X-Operator-Token": "wrong-token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_accepts_correct_token(auth_client):
    assert (await auth_client.get("/api/dashboard")).status_code == 200
    assert (await auth_client.get("/api/threads/posts")).status_code == 200


@pytest.mark.asyncio
async def test_exempt_locations_stay_open_without_token(open_client):
    assert (await open_client.get("/api/health")).status_code == 200
    assert (await open_client.get("/api/health/live")).status_code == 200
    # Readiness depends on MySQL+Redis availability, not on the operator
    # token: in the test env Redis is unreachable, so the honest answer is a
    # dependency 503 — what matters is that the route is NOT 401.
    ready = await open_client.get("/api/health/ready")
    assert ready.status_code == 503
    assert "token" not in ready.text.lower()
    # OAuth callback: Meta redirects the browser here, a header cannot be set
    # by the operator. Wrong state must fail with the domain error, not 401.
    assert (await open_client.get("/api/threads/oauth/callback?code=x&state=bad")).status_code != 401
    assert (await open_client.get("/api/social/oauth/instagram/callback?code=x&state=bad")).status_code != 401
    # Webhook: GET challenge without verify token fails with 403, not 401.
    assert (await open_client.get("/api/threads/webhook?hub.mode=subscribe&hub.challenge=1")).status_code == 403


@pytest.mark.asyncio
async def test_unknown_social_platform_callback_is_exempt(open_client):
    """A future platform (e.g. tiktok) may not accidentally inherit the 401."""
    response = await open_client.get("/api/social/oauth/tiktok/callback?code=x&state=bad")
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_seed_requires_token(auth_client):
    response = await auth_client.post("/api/seed")
    assert response.status_code == 200
    assert response.json() == {"seeded": True}


@pytest.mark.asyncio
async def test_seed_rejected_without_token(open_client):
    assert (await open_client.post("/api/seed")).status_code == 401


@pytest.mark.asyncio
async def test_seed_live_mode_forbidden(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    response = await auth_client.post("/api/seed")
    assert response.status_code == 403
    assert "mock" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_post_without_secret_in_live_mode_rejected(open_client, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_app_secret", "")
    response = await open_client.post("/api/threads/webhook", json={"entry": []})
    assert response.status_code == 403
    assert "signature" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_post_without_secret_in_mock_mode_allowed(open_client):
    # conftest defaults: THREADS_MOCK_MODE=true, THREADS_APP_SECRET empty.
    response = await open_client.post("/api/threads/webhook", json={"entry": []})
    assert response.status_code == 200
    assert response.json() == {"status": "received"}


@pytest.mark.asyncio
async def test_missing_token_in_development_still_answers(db, monkeypatch):
    async with _build_client(db, with_token=False) as http:
        monkeypatch.setattr(settings, "operator_token", "")
        monkeypatch.setattr(settings, "app_env", "development")
        response = await http.get("/api/dashboard")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_missing_token_outside_development_fails_closed(db, monkeypatch):
    async with _build_client(db, with_token=False) as http:
        monkeypatch.setattr(settings, "operator_token", "")
        monkeypatch.setattr(settings, "app_env", "production")
        response = await http.get("/api/dashboard")
        assert response.status_code == 503
        assert "token" in response.json()["detail"].lower()
        # The exact feature must not be inferable from the error.
        assert "dashboard" not in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_missing_token_outside_development_exempt_routes_still_work(db, monkeypatch):
    async with _build_client(db, with_token=False) as http:
        monkeypatch.setattr(settings, "operator_token", "")
        monkeypatch.setattr(settings, "app_env", "production")
        response = await http.get("/api/health")
        assert response.status_code == 200
