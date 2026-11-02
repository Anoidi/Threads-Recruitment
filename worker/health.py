import asyncio
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import SyncState


async def check() -> None:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.5)
        value = await redis.get("threads:worker:heartbeat")
        await redis.aclose()
        if value:
            return
    except Exception:
        pass
    async with SessionLocal() as db:
        row = (await db.execute(select(SyncState).where(SyncState.resource == "worker_heartbeat"))).scalar_one_or_none()
        if not row or not row.last_success_at:
            raise SystemExit(1)
        stamp = row.last_success_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp < datetime.now(timezone.utc) - timedelta(seconds=settings.worker_heartbeat_ttl):
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(check())

