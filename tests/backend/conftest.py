import os
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
os.environ.update({
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "THREADS_MOCK_MODE": "true",
    "AI_MOCK_MODE": "true",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "OPERATOR_TOKEN": "test-operator-token",
    # Analytics responses must never be cached between fixtures in tests.
    "ANALYTICS_CACHE_SECONDS": "0",
})

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import create_app

engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
TestingSession = async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    async with TestingSession() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db):
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testclient",
        headers={"X-Operator-Token": "test-operator-token"},
    ) as http:
        yield http


@pytest_asyncio.fixture
async def vacancy(client):
    response = await client.post("/api/vacancies", json={
        "title": "Warehouse worker", "description": "Pick and pack orders", "location": "Kraków",
        "salary": "30 PLN gross/hour", "schedule": "2 shifts", "language": "en",
    })
    assert response.status_code == 201
    return response.json()


async def set_autonomy_mode(db, mode: str, **overrides):
    """Put the autonomy policy into a known state for a test.

    The policy is the single source of truth for what may run, so a test
    that expects automatic behaviour has to say so explicitly instead of
    relying on a legacy flag.
    """
    from app.autonomy import DEFAULT_LIMITS, DEFAULT_REPLY_TOPICS, ensure_policy
    from app.models import AutonomyPolicy

    row = (await db.execute(select(AutonomyPolicy).order_by(AutonomyPolicy.id.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        row = AutonomyPolicy()
        db.add(row)
    row.mode = mode
    row.limits = dict(DEFAULT_LIMITS)
    row.reply_topics = list(DEFAULT_REPLY_TOPICS)
    row.platform_allowlist = ["threads"]
    for key, value in overrides.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


