import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_health_is_secret_free_and_reports_mock_mode(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["components"]["backend"]["status"] == "ONLINE"
    assert "token" not in str(body).lower()


@pytest.mark.asyncio
async def test_readiness_checks_backend_dependencies(client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.api import Redis
    redis = AsyncMock()
    redis.ping.return_value = True
    monkeypatch.setattr(Redis, 'from_url', lambda *args, **kwargs: redis)
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "READY"}
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_rejects_unavailable_redis(client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.api import Redis
    redis = AsyncMock()
    redis.ping.side_effect = RuntimeError('private dependency detail')
    monkeypatch.setattr(Redis, 'from_url', lambda *args, **kwargs: redis)
    response = await client.get('/api/health/ready')
    assert response.status_code == 503
    assert response.json() == {'detail': 'backend dependencies are not ready'}
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_vacancy_crud_filter_and_seed(client, vacancy):
    response = await client.get("/api/vacancies", params={"q": "Krak", "active": True})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    updated = await client.patch(f"/api/vacancies/{vacancy['id']}", json={"housing": "Optional shared housing"})
    assert updated.status_code == 200
    assert updated.json()["housing"] == "Optional shared housing"
    seeded = await client.post("/api/seed")
    assert seeded.status_code == 200


@pytest.mark.asyncio
async def test_real_mode_without_meta_credentials_is_controlled(client, monkeypatch):
    monkeypatch.setattr(settings, "threads_mock_mode", False)
    monkeypatch.setattr(settings, "threads_app_id", "")
    monkeypatch.setattr(settings, "threads_app_secret", "")
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["mode"] == "live"
    assert health.json()["components"]["threads"] == {
        "status": "DEGRADED",
        "detail": "missing credentials (THREADS_APP_ID / THREADS_APP_SECRET)",
    }
    oauth = await client.get("/api/threads/oauth/start")
    assert oauth.status_code == 501
    assert "configured" in oauth.json()["detail"].lower()

