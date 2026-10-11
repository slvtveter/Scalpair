"""Alert rules CRUD, notification inbox and Telegram linking (auth-scoped)."""

from __future__ import annotations

import os
import re
import secrets
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.auth import User, current_user
from app.config import get_settings
from app.db import AlertEvent, AlertRule, NotificationDelivery, TelegramLink, UserPreference, WatchlistItem, get_session_factory

router = APIRouter(prefix="/api/v1", tags=["alerts"])

AUTHORIZED = Annotated[User, Depends(current_user)]


def _require_user(user: User | None) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in to manage alerts")
    return user


class WatchlistIn(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=500)


class PreferencesIn(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


@router.get("/preferences")
async def get_preferences(user: AUTHORIZED) -> dict[str, Any]:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        row = await sess.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
    return {"preferences": row.payload if row else {}}


@router.put("/preferences")
async def put_preferences(body: PreferencesIn, user: AUTHORIZED) -> dict[str, Any]:
    _require_user(user)
    # Keep this endpoint a workspace store rather than an arbitrary JSON sink.
    allowed = {
        "groups", "hidden", "order", "filters", "filterPresets", "grid", "sort",
        "sortAscending", "autoSort", "boardTf", "showLevels", "showWalls", "sound",
        "dndUntil", "syncCrosshair", "syncRange", "paneOverlays", "focusLayout",
        "focusVenue",
    }
    payload = {key: value for key, value in body.preferences.items() if key in allowed}
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        row = await sess.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
        if row is None:
            row = UserPreference(user_id=user.id, payload=payload)
            sess.add(row)
        else:
            row.payload = payload
        await sess.commit()
    return {"preferences": payload}


@router.get("/watchlists")
async def get_watchlist(user: AUTHORIZED) -> dict[str, Any]:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rows = (
            await sess.execute(select(WatchlistItem).where(WatchlistItem.user_id == user.id).order_by(WatchlistItem.id))
        ).scalars().all()
    return {"symbols": [row.instrument_id or row.symbol for row in rows]}


@router.put("/watchlists")
async def put_watchlist(body: WatchlistIn, user: AUTHORIZED) -> dict[str, Any]:
    _require_user(user)
    symbols: list[str] = []
    for raw in body.symbols:
        symbol = raw.strip().upper()
        if not re.fullmatch(r"(?:(?:BI-F:FUTURES|BY-F:FUTURES|OK-F:FUTURES|BI-S:SPOT|BY-S:SPOT|DEMO:FUTURES):)?[A-Z0-9]{1,28}USDT", symbol):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "watchlist symbols must be USDT instruments")
        if symbol not in symbols:
            symbols.append(symbol)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rows = (
            await sess.execute(select(WatchlistItem).where(WatchlistItem.user_id == user.id))
        ).scalars().all()
        for row in rows:
            await sess.delete(row)
        sess.add_all([WatchlistItem(user_id=user.id, symbol=symbol.split(":")[-1], instrument_id=symbol if ":" in symbol else None) for symbol in symbols])
        await sess.commit()
    return {"symbols": symbols}


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


class RulePatch(BaseModel):
    enabled: bool | None = None
    threshold: float | None = Field(default=None, gt=0)
    cooldown_s: int | None = Field(default=None, ge=30, le=86400)
    recurring: bool | None = None


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


@router.patch("/alerts/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, body: RulePatch, user: AUTHORIZED) -> RuleOut:
    _require_user(user)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        rule = await sess.get(AlertRule, rule_id)
        if not rule or rule.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(rule, key, value)
        await sess.commit()
        await sess.refresh(rule)
        return _rule_out(rule)


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
                    "instrument_id": e.instrument_id,
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


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    """Consume Telegram /start deep-links and bind the verified chat id.

    Telegram can authenticate webhook deliveries with a secret header. When
    configured, reject every request without the exact secret; the short-lived
    random deep-link token remains a second, user-specific proof.
    """
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if expected_secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid Telegram webhook secret")
    try:
        update = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Telegram update") from exc
    message = update.get("message") if isinstance(update, dict) else None
    chat = message.get("chat") if isinstance(message, dict) else None
    text = message.get("text") if isinstance(message, dict) else None
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(text, str):
        return {"ok": True, "linked": False}
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].split("@", 1)[0].lower() != "/start" or len(parts) != 2:
        return {"ok": True, "linked": False}
    token = parts[1].strip()
    if not token or len(token) > 64:
        return {"ok": True, "linked": False}
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        link = (
            await sess.execute(
                select(TelegramLink).where(
                    TelegramLink.link_token == token,
                    TelegramLink.token_expires > time.time(),
                )
            )
        ).scalar_one_or_none()
        if link is None:
            return {"ok": True, "linked": False}
        link.chat_id = str(chat.get("id", ""))
        link.linked_at = time.time()
        link.link_token = None
        link.token_expires = None
        await sess.commit()
    return {"ok": True, "linked": True}
