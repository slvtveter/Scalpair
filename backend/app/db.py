"""Async database layer — SQLAlchemy 2.0 + aiosqlite (SQLite WAL by default).

Holds SaaS scaffolding: users with subscription tiers, watchlists, and the
historical alert log. Swap DATABASE_URL to PostgreSQL for production without
touching this module.
"""

from __future__ import annotations

import logging
import os
import time
from typing import AsyncIterator

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = logging.getLogger("scalpair.db")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    subscription_tier: Mapped[str] = mapped_column(String(16), default="free")  # free|pro|whale
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)
    tag: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str):
    global _engine
    if _engine is None:
        if database_url.startswith("sqlite"):
            db_path = database_url.split("///")[-1]
            if db_path and db_path != ":memory:":
                os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        _engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)

        if database_url.startswith("sqlite"):

            @event.listens_for(_engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _engine


def get_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(database_url), expire_on_commit=False)
    return _session_factory


async def init_db(database_url: str) -> None:
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if database_url.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
    log.info("database ready (%s)", database_url.split("://")[0])


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def session_scope(database_url: str) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(database_url)
    async with factory() as sess:
        yield sess
