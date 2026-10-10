"""Server-side alert rules engine (spec section 7).

Evaluates armed rules every cycle regardless of browser state. Edge-triggered
crossing (a rule fires on the *transition* through the threshold, not while
it stays beyond it), per-rule cooldown/re-arm, idempotent event ids, and a
durable delivery table (in_app always; telegram when linked and the bot token
is configured — honestly marked `unconfigured` otherwise).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

import aiohttp
from sqlalchemy import select

from app.config import Settings
from app.db import AlertEvent, AlertRule, NotificationDelivery, TelegramLink, get_session_factory
from app.state import MarketState
from app.instruments import venue_for_feed

log = logging.getLogger("scalpair.alerts")

RULE_TYPES = {"price_cross_above", "price_cross_below", "pct_move", "volume_surge", "score_above", "cascade_distance", "density_appeared"}


class AlertEngine:
    EVAL_INTERVAL_S = 2.0
    DELIVERY_RETRY_S = 10.0
    MAX_ATTEMPTS = 3

    def __init__(self, state: MarketState, settings: Settings, scorer) -> None:
        self.state = state
        self.settings = settings
        self.scorer = scorer
        self._stop = asyncio.Event()
        self.telegram_configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    async def run(self) -> None:
        delivery_clock = 0.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self._evaluate()
            except Exception:  # noqa: BLE001 — the loop must survive everything
                log.exception("alert evaluation cycle failed")
            if time.monotonic() - delivery_clock >= self.DELIVERY_RETRY_S:
                delivery_clock = time.monotonic()
                try:
                    await self._deliver_pending()
                except Exception:  # noqa: BLE001
                    log.exception("alert delivery cycle failed")
            await asyncio.sleep(max(0.5, self.EVAL_INTERVAL_S - (time.monotonic() - started)))

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _rule_matches_instrument(self, rule: AlertRule) -> bool:
        if not rule.instrument_id:
            return True
        current_id = f"{venue_for_feed(self.state.feed_mode)}:FUTURES:{rule.symbol.upper()}"
        return current_id == rule.instrument_id

    async def _evaluate(self) -> None:
        factory = get_session_factory(self.settings.database_url)
        async with factory() as sess:
            rules = (await sess.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars().all()
            now_s = time.time()
            for rule in rules:
                if not self._rule_matches_instrument(rule):
                    continue
                if rule.last_triggered_at and now_s - rule.last_triggered_at < rule.cooldown_s:
                    continue
                price = self.state.symbols.get(rule.symbol.upper()).price if rule.symbol.upper() in self.state.symbols else None
                fired, observed = self._check(rule, price)
                if not fired:
                    # keep the anchor updated for edge detection
                    if rule.rule_type in ("price_cross_above", "price_cross_below") and price is not None:
                        rule.last_value = price
                    elif rule.rule_type in ("cascade_distance", "density_appeared"):
                        if rule.rule_type == "cascade_distance":
                            rule.last_value = 1.0 if observed is not None and observed <= rule.threshold else 0.0
                        else:
                            rule.last_value = 1.0 if observed is not None and observed >= rule.threshold else 0.0
                    continue
                event_id = uuid.uuid4().hex
                message = self._message(rule, observed)
                sess.add(
                    AlertEvent(
                        rule_id=rule.id,
                        user_id=rule.user_id,
                        event_id=event_id,
                        ts=now_s,
                        symbol=rule.symbol,
                        rule_type=rule.rule_type,
                        message=message,
                    )
                )
                await sess.flush()
                event_row = (
                    await sess.execute(select(AlertEvent).where(AlertEvent.event_id == event_id))
                ).scalar_one()
                sess.add(NotificationDelivery(event_db_id=event_row.id, channel="in_app"))
                if await self._user_has_telegram(sess, rule.user_id):
                    sess.add(NotificationDelivery(event_db_id=event_row.id, channel="telegram"))
                if rule.rule_type in ("cascade_distance", "density_appeared"):
                    rule.last_value = 1.0
                else:
                    rule.last_value = price if price is not None else rule.threshold
                rule.last_triggered_at = now_s
                if not rule.recurring:
                    rule.enabled = False
                log.info("alert fired: %s %s %s observed=%s", rule.symbol, rule.rule_type, rule.threshold, observed)
            await sess.commit()

    def _check(self, rule: AlertRule, price: float | None) -> tuple[bool, float | None]:
        """Returns (fired, observed_value). Edge semantics for crossings."""
        if rule.rule_type in ("price_cross_above", "price_cross_below", "pct_move"):
            if price is None or price <= 0:
                return False, None
            prev = rule.last_value
            if rule.rule_type == "price_cross_above":
                fired = prev is not None and prev <= rule.threshold < price
                return fired, price
            if rule.rule_type == "price_cross_below":
                fired = prev is not None and prev >= rule.threshold > price
                return fired, price
            # pct_move over the configured window: anchor = price window_s ago
            sym_state = self.state.symbols.get(rule.symbol.upper())
            hist = list(sym_state.price_history) if sym_state else []
            target_ms = time.time() * 1000 - rule.window_s * 1000
            base = None
            for ts, p in hist:
                if ts <= target_ms:  # ts is already ms (state stores ms)
                    base = p
                else:
                    break
            if base is None or base <= 0:
                return False, None
            moved = abs(price / base - 1) * 100.0
            return moved >= rule.threshold, moved
        if rule.rule_type == "volume_surge":
            sym = self.state.symbols.get(rule.symbol.upper())
            surge = sym.metrics.surge_closed if sym else None
            return (surge is not None and surge >= rule.threshold), surge
        if rule.rule_type == "score_above":
            pick = self.scorer.picks.get(rule.symbol.upper())
            score = pick.scalp_score if pick else None
            return (score is not None and score >= rule.threshold), score
        if rule.rule_type == "cascade_distance":
            sym = self.state.symbols.get(rule.symbol.upper())
            if not sym or not price or price <= 0: return False, None
            distances = [abs(price - z.mid) / price * 100.0 for z in sym.cascades if z.mid > 0]
            distance = min(distances, default=None)
            condition = distance is not None and distance <= rule.threshold
            fired = condition and rule.last_value is not None and rule.last_value < 0.5
            return fired, distance
        if rule.rule_type == "density_appeared":
            sym = self.state.symbols.get(rule.symbol.upper())
            if not sym: return False, None
            largest = max((float(w.get("notional_usd", 0)) for w in sym.walls.values()), default=0.0)
            condition = largest >= rule.threshold
            fired = condition and rule.last_value is not None and rule.last_value < 0.5
            return fired, largest
        return False, None

    def _message(self, rule: AlertRule, observed: float | None) -> str:
        obs = f" (now {observed})" if observed is not None else ""
        if rule.rule_type == "price_cross_above":
            return f"{rule.symbol} crossed ABOVE {rule.threshold:g}{obs}"
        if rule.rule_type == "price_cross_below":
            return f"{rule.symbol} crossed BELOW {rule.threshold:g}{obs}"
        if rule.rule_type == "pct_move":
            return f"{rule.symbol} moved {observed:.2f}% ≥ {rule.threshold:g}% within {rule.window_s}s"
        if rule.rule_type == "volume_surge":
            return f"{rule.symbol} volume surge {observed:.1f}x ≥ {rule.threshold:g}x"
        if rule.rule_type == "cascade_distance":
            return f"{rule.symbol} is {observed:.3f}% from cascade ≤ {rule.threshold:g}%"
        if rule.rule_type == "density_appeared":
            return f"{rule.symbol} large density appeared: {observed:,.0f} USDT ≥ {rule.threshold:,.0f}"
        return f"{rule.symbol} scalp score {observed:.0f} ≥ {rule.threshold:g}"

    async def _user_has_telegram(self, sess, user_id: int) -> bool:
        link = (
            await sess.execute(select(TelegramLink).where(TelegramLink.user_id == user_id))
        ).scalar_one_or_none()
        return bool(link and link.chat_id)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def _deliver_pending(self) -> None:
        factory = get_session_factory(self.settings.database_url)
        async with factory() as sess:
            pending = (
                await sess.execute(
                    select(NotificationDelivery).where(NotificationDelivery.status == "pending")
                )
            ).scalars().all()
            for d in pending:
                if d.channel == "in_app":
                    d.status, d.sent_at = "sent", time.time()  # inbox poll = delivery
                    continue
                if d.channel == "telegram":
                    if not self.telegram_configured:
                        d.status, d.last_error = "unconfigured", "TELEGRAM_BOT_TOKEN not set"
                        continue
                    if d.attempts >= self.MAX_ATTEMPTS:
                        d.status, d.last_error = "failed", "max attempts"
                        continue
                    d.attempts += 1
                    event = await sess.get(AlertEvent, d.event_db_id)
                    link = (
                        await sess.execute(
                            select(TelegramLink).where(TelegramLink.user_id == event.user_id)
                        )
                    ).scalar_one_or_none()
                    if not link or not link.chat_id:
                        d.status, d.last_error = "failed", "no chat linked"
                        continue
                    ok, err = await self._send_telegram(link.chat_id, event.message)
                    if ok:
                        d.status, d.sent_at = "sent", time.time()
                    else:
                        d.last_error = (err or "send failed")[:255]
                        if d.attempts >= self.MAX_ATTEMPTS:
                            d.status = "failed"
            await sess.commit()

    async def _send_telegram(self, chat_id: str, text: str) -> tuple[bool, str | None]:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(url, json={"chat_id": chat_id, "text": f"Scalpair: {text}"}) as resp:
                    if resp.status == 200:
                        return True, None
                    return False, f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001
            return False, repr(exc)[:200]
