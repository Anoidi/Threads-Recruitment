from collections.abc import AsyncGenerator
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# aiomysql's async adapter does not implement SQLAlchemy's synchronous
# pool_pre_ping hook correctly (it calls ping() without the reconnect arg).
# Connection failures are handled by the request/worker retry paths instead.
# pool_recycle keeps long-lived worker sessions from colliding with MySQL's
# server-side idle connection timeout (audit I-10/D8): a recycled connection
# is rebuilt instead of failing mid-job.
engine = create_async_engine(settings.database_url, pool_pre_ping=False, pool_recycle=1800)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

