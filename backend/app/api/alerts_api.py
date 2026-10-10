"""Alert rules CRUD, notification inbox and Telegram linking (auth-scoped)."""

from __future__ import annotations

import os
import secrets
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.auth import User, current_user
from app.config import get_settings
from app.db import AlertEvent, AlertRule, NotificationDelivery, TelegramLink, get_session_factory

router = APIRouter(prefix="/api/v1", tags=["alerts"])

AUTHORIZED = Annotated[User, Depends(current_user)]


def _require_user(user: User | None) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in to manage alerts")
    return user


class RuleIn(BaseModel):
    symbol: str = Field(min_length=5, max_length=32)
    instrument_id: str | None = Field(default=None, min_length=3, max_length=128)
    rule_type: Literal["price_cross_above", "price_cross_below", "pct_move", "volume_surge", "score_above", "cascade_distance", "density_appeared"]
    threshold: float = Field(gt=0)
    window_s: int = Field(default=60, ge=10, le=3600)
    cooldown_s: int = Field(default=300, ge=30, le=86400)
    recurring: bool = True


class RuleOut(RuleIn):
    id: int
    enabled: bool
    last_triggered_at: float | None = None


def _rule_out(r: AlertRule) -> RuleOut:
    return RuleOut(
        id=r.id, symbol=r.symbol, instrument_id=r.instrument_id, rule_type=r.rule_type, threshold=r.threshold,
        window_s=r.window_s, cooldown_s=r.cooldown_s, recurring=r.recurring,
        enabled=r.enabled, last_triggered_at=r.last_triggered_at,
    )


@router.post("/alerts", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleIn, user: AUTHORIZED) -> RuleOut:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rule = AlertRule(
            user_id=user.id, symbol=body.symbol.upper(), instrument_id=body.instrument_id, rule_type=body.rule_type,
            threshold=body.threshold, window_s=body.window_s, cooldown_s=body.cooldown_s,
            recurring=body.recurring,
        )
        sess.add(rule)
        await sess.commit()
        await sess.refresh(rule)
        return _rule_out(rule)


@router.get("/alerts", response_model=list[RuleOut])
async def list_rules(user: AUTHORIZED) -> list[RuleOut]:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rules = (
            await sess.execute(select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id))
        ).scalars().all()
        return [_rule_out(r) for r in rules]


@router.delete("/alerts/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, user: AUTHORIZED) -> None:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rule = await sess.get(AlertRule, rule_id)
        if not rule or rule.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
        await sess.delete(rule)
        await sess.commit()


@router.get("/notifications")
async def notifications(user: AUTHORIZED) -> dict[str, Any]:
    """In-app inbox: events for the user's rules + delivery statuses."""
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        events = (
            await sess.execute(
                select(AlertEvent).where(AlertEvent.user_id == user.id).order_by(AlertEvent.id.desc()).limit(50)
            )
        ).scalars().all()
        ids = [e.id for e in events]
        deliveries = (
            await sess.execute(
                select(NotificationDelivery).where(NotificationDelivery.event_db_id.in_(ids))
            )
        ).scalars().all() if ids else []
        by_event: dict[int, list[str]] = {}
        for d in deliveries:
            by_event.setdefault(d.event_db_id, []).append(f"{d.channel}:{d.status}")
        return {
            "ts": time.time(),
            "notifications": [
                {
                    "id": e.id,
                    "event_id": e.event_id,
                    "ts": e.ts,
                    "symbol": e.symbol,
                    "rule_type": e.rule_type,
                    "message": e.message,
                    "delivery": by_event.get(e.id, []),
                }
                for e in events
            ],
        }


# ---------------------------------------------------------------------------
# Telegram linking (spec: one-time deep-link token + /start handshake)
# ---------------------------------------------------------------------------
@router.post("/telegram/link")
async def telegram_link(user: AUTHORIZED) -> dict[str, Any]:
    _require_user(user)
    bot_configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        link = (
            await sess.execute(select(TelegramLink).where(TelegramLink.user_id == user.id))
        ).scalar_one_or_none()
        if not bot_configured:
            # honest unconfigured state: the rest of the product keeps working
            return {"configured": False, "env": "TELEGRAM_BOT_TOKEN", "chat_linked": bool(link and link.chat_id)}
        if not link:
            link = TelegramLink(user_id=user.id)
            sess.add(link)
        link.link_token = secrets.token_urlsafe(24)
        link.token_expires = time.time() + 600  # short-lived one-time token
        await sess.commit()
        bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "ScalpairBot")
        return {
            "configured": True,
            "chat_linked": bool(link.chat_id),
            "link_url": f"https://t.me/{bot_username}?start={link.link_token}",
            "expires_s": 600,
        }


@router.delete("/telegram/link", status_code=204)
async def telegram_unlink(user: AUTHORIZED) -> None:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        link = (
            await sess.execute(select(TelegramLink).where(TelegramLink.user_id == user.id))
        ).scalar_one_or_none()
        if link:
            await sess.delete(link)
            await sess.commit()
