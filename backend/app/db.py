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

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, event, text
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


class ChartDrawing(Base):
    """User-owned Focus drawings keyed by symbol."""
    __tablename__ = "chart_drawings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    drawings: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)
    tag: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AlertRule(Base):
    """Server-side alert rule (spec section 7) — evaluated with the browser closed."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule_type: Mapped[str] = mapped_column(String(32))  # price_cross_above|price_cross_below|pct_move|volume_surge|score_above
    threshold: Mapped[float] = mapped_column(Float)
    window_s: Mapped[int] = mapped_column(Integer, default=60)     # for pct_move
    cooldown_s: Mapped[int] = mapped_column(Integer, default=300)  # re-arm delay
    recurring: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # edge-detection anchor
    last_triggered_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)  # idempotency
    ts: Mapped[float] = mapped_column(Float, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    rule_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(512))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_db_id: Mapped[int] = mapped_column(ForeignKey("alert_events.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # in_app | telegram
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|unconfigured
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    sent_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TelegramLink(Base):
    __tablename__ = "telegram_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    link_token: Mapped[str | None] = mapped_column(String(64), nullable=True)  # one-time deep-link token
    token_expires: Mapped[float | None] = mapped_column(Float, nullable=True)
    linked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


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
            # create_all does not alter existing tables; keep Render's
            # persistent SQLite database compatible with the new field.
            columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(alert_rules)"))).all()}
            if "instrument_id" not in columns:
                await conn.execute(text("ALTER TABLE alert_rules ADD COLUMN instrument_id VARCHAR(128)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alert_rules_instrument_id ON alert_rules (instrument_id)"))
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
