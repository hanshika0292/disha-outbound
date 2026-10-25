from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from disha.config import get_settings


# Connection-resilience options. Without these, connections that Fly Postgres
# (or the network) silently drops during idle periods stay in the pool as
# stale handles — the next request grabs a dead connection and the whole call
# fails, surfacing as "API offline" in the UI after the app sits unused.
#
#   pool_pre_ping  — emit a lightweight SELECT 1 before handing out a pooled
#                    connection; if it's dead, transparently reconnect.
#   pool_recycle   — proactively retire connections older than 5 min so we
#                    never hand out one Postgres is about to close.
_ENGINE_KW = dict(future=True, pool_pre_ping=True, pool_recycle=300)


@lru_cache(maxsize=1)
def admin_engine():  # type: ignore[no-untyped-def]
    return create_async_engine(get_settings().database_url_admin, **_ENGINE_KW)


@lru_cache(maxsize=1)
def app_engine():  # type: ignore[no-untyped-def]
    return create_async_engine(get_settings().database_url_app, **_ENGINE_KW)


@lru_cache(maxsize=1)
def admin_sessionmaker_() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(admin_engine(), class_=AsyncSession, expire_on_commit=False)


@lru_cache(maxsize=1)
def app_sessionmaker_() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine(), class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """Session as `disha_admin` (BYPASSRLS). Use for migrations, seeds, platform ops."""
    async with admin_sessionmaker_()() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def tenant_session(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """Session as `disha_app` with RLS scoped to `tenant_id`.

    `set_config(..., is_local=true)` is the SET LOCAL equivalent — scoped to the
    enclosing transaction. If anyone uses this session outside `session.begin()`,
    the setting evaporates and RLS returns zero rows. That's intentional.
    """
    async with app_sessionmaker_()() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session
