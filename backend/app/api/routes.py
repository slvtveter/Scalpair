"""REST endpoints + live WebSocket feed.

Public market data is free for everyone. The `tier` mechanism demonstrates
freemium gating: `free` clients receive only the top-10 symbols and a
throttled update stream; `pro`/`whale` get everything at full rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from app.api.auth import User, current_user
from app.instruments import venue_for_feed
from app.config import get_settings
from app.models import HealthReport
from app.history import TIMEFRAMES, aggregate_history

log = logging.getLogger("scalpair.api")

router = APIRouter(prefix="/api/v1", tags=["screeners"])

START_TIME = time.time()

FREE_TIER_SYMBOLS = 10
FREE_TIER_UPDATE_DIVISOR = 4  # ~15s effective delay at 0.5Hz metrics cadence


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthReport)
async def health(request: Request) -> HealthReport:
    state = request.app.state.market_state
    streamer = request.app.state.streamer
    broadcaster = request.app.state.broadcaster
    last_age = state.message_age_ms()
    feeds = [streamer.feed.name] if streamer and streamer.feed else []
    feeds += [f.name for f in (streamer.extra_feeds if streamer else [])]
    uptime = time.time() - START_TIME
    # A connector can pass the universe probe and still fail to ingest WS
    # events. After a short warmup that state is degraded, never healthy.
    no_ingest_after_warmup = bool(streamer and streamer.feed and state.messages_ingested == 0 and uptime > 30)
    return HealthReport(
        status="ok" if not streamer.feed_degraded and not no_ingest_after_warmup and (last_age is None or last_age < 30_000) else "degraded",
        uptime_s=round(uptime, 1),
        active_feeds=feeds,
        active_ws_clients=broadcaster.client_count,
        tracked_symbols=len(streamer.top_symbols) if streamer else 0,
        messages_ingested=state.messages_ingested,
        avg_ingest_latency_ms=round(state.ingest_latency_ema_ms, 2),
        last_message_age_ms=round(last_age) if last_age is not None else None,
        redis_connected=broadcaster.redis_connected,
        feed_mode=state.feed_mode,
    )


# ---------------------------------------------------------------------------
# Screeners
# ---------------------------------------------------------------------------
@router.get("/screeners/densities")
async def densities(request: Request, limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    """All active density walls across tracked markets, biggest first."""
    state = request.app.state.market_state
    streamer = request.app.state.streamer
    feed_name = streamer.feed.name if streamer.feed else ""
    venue = venue_for_feed(feed_name)
    walls = []
    for sym in streamer.top_symbols:
        st = state.symbols.get(sym)
        if not st:
            continue
        for w in st.walls.values():
            walls.append(
                {
                    "symbol": sym,
                    "venue": venue,
                    "side": w["side"],
                    "price": w["price"],
                    "notional_usd": w["notional_usd"],
                    "qty": w["qty"],
                    "distance_pct": w["distance_pct"],
                    "seconds_to_eat": w.get("seconds_to_eat"),
                    "detected_at": w["detected_at"],
                    "age_s": w.get("age_s", 0.0),
                    "book_ts": st.book_ts_ms,
                }
            )
    walls.sort(key=lambda w: w["notional_usd"], reverse=True)
    return JSONResponse({"ts": int(time.time() * 1000), "walls": walls[:limit], "total": len(walls), "feed": state.feed_mode})


@router.get("/screeners/ai-picks")
async def ai_picks(
    request: Request,
    top: int = Query(5, ge=1, le=50),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    user: User | None = Depends(current_user),
) -> JSONResponse:
    """Top assets by Scalp Alpha Score with setup thesis."""
    scorer = request.app.state.scorer
    picks = scorer.top(top, min_score)
    return JSONResponse(
        {
            "ts": int(time.time() * 1000),
            "tier": user.subscription_tier if user else "anonymous",
            "picks": [p.model_dump() for p in picks],
        }
    )


@router.get("/screeners/overview")
async def overview(request: Request) -> JSONResponse:
    """Full terminal table payload (same shape as the WS snapshot symbols array)."""
    streamer = request.app.state.streamer
    return JSONResponse({"ts": int(time.time() * 1000), "symbols": streamer.build_snapshot()["symbols"]})


@router.get("/screeners/score-alerts")
async def recent_alerts(request: Request) -> JSONResponse:
    streamer = request.app.state.streamer
    return JSONResponse({"ts": int(time.time() * 1000), "alerts": streamer.alerts[-25:]})


@router.get("/markets/symbols")
async def symbols(request: Request) -> JSONResponse:
    streamer = request.app.state.streamer
    return JSONResponse(
        {
            "feed": request.app.state.market_state.feed_mode,
            "symbols": [
                {
                    "symbol": s,
                    "instrument_id": f"{venue_for_feed(streamer.feed.name if streamer.feed else "")}:FUTURES:{s}",
                    "price": request.app.state.market_state.symbols[s].price,
                    "quote_volume_24h": request.app.state.market_state.symbols[s].quote_volume_24h,
                    "venue": venue_for_feed(streamer.feed.name if streamer.feed else ""),
                    "market": "FUTURES",
                }
                for s in streamer.top_symbols if s in request.app.state.market_state.symbols
            ],
        }
    )


@router.get("/markets/{symbol}/candles")
async def candles(
    request: Request,
    symbol: str,
    limit: int = Query(300, ge=10, le=500),
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D"] = "1m",
) -> JSONResponse:
    """1-minute OHLCV candles + current walls for the chart view."""
    state = request.app.state.market_state
    st = state.symbols.get(symbol.upper())
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown symbol {symbol}")
    streamer = request.app.state.streamer
    feed, generation = streamer.feed, streamer._generation
    source = "stream"
    history = aggregate_history(list(st.candles), TIMEFRAMES[timeframe])[-limit:]
    limited = timeframe != "1m"
    if timeframe != "1m" and feed is not None and hasattr(feed, "fetch_klines"):
        try:
            history = await streamer.history.get(generation, feed, st.symbol, timeframe, limit)
        except Exception as exc:
            raise HTTPException(503, "Exchange history temporarily unavailable") from exc
        if generation != streamer._generation or feed is not streamer.feed:
            raise HTTPException(409, "Market source changed; retry history request")
        source = "exchange_rest"
        limited = False
    return JSONResponse(
        {
            "symbol": st.symbol,
            "instrument_id": f"{venue_for_feed(feed.name if feed else "")}:FUTURES:{st.symbol}",
            "timeframe": timeframe,
            "feed": state.feed_mode,
            "venue": venue_for_feed(feed.name if feed else ""),
            "sourceGeneration": generation,
            "historySource": source,
            "limitedHistory": limited,
            "levelTimeframe": "1m",
            "candles": history,
            "walls": [
                {
                    "side": w["side"],
                    "price": w["price"],
                    "notional": w["notional_usd"],
                    "age_s": w.get("age_s", 0.0),
                }
                for w in sorted(st.walls.values(), key=lambda w: w["notional_usd"], reverse=True)[:6]
            ],
            "levels": [
                {
                    "kind": z.kind,
                    "low": z.low,
                    "high": z.high,
                    "mid": z.mid,
                    "touches": z.touches,
                }
                for z in sorted(st.cascades, key=lambda z: z.touches, reverse=True)[:10]
            ],
            "price": st.price,
        }
    )


# ---------------------------------------------------------------------------
# Live WebSocket
# ---------------------------------------------------------------------------
def _apply_tier(snapshot: dict[str, Any], tier: str, tick_counter: int) -> dict[str, Any] | None:
    """Freemium gating: free tier sees top-10 symbols at 1/4 update rate."""
    if tier in ("pro", "whale"):
        return snapshot
    if tick_counter % FREE_TIER_UPDATE_DIVISOR != 0:
        return None
    trimmed = dict(snapshot)
    trimmed["symbols"] = snapshot.get("symbols", [])[:FREE_TIER_SYMBOLS]
    trimmed["tier"] = "free (delayed)"
    return trimmed


@router.websocket("/ws/live-feed")
async def live_feed(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    broadcaster = app.state.broadcaster
    tier = "free"
    # optional auth: client may pass a JWT as the first message.
    # An invalid token must close politely with policy-violation code 1008 —
    # raising inside a websocket scope makes ASGI attempt an HTTP error
    # response and produces an opaque connection reset + server traceback.
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        try:
            msg = json.loads(first)
            if isinstance(msg, dict) and msg.get("token"):
                from app.api.auth import decode_token

                try:
                    payload = decode_token(msg["token"])
                    tier = payload.get("tier", "free")
                except HTTPException:
                    await ws.close(code=1008, reason="invalid token")
                    return
        except json.JSONDecodeError:
            tier = "free"  # non-JSON greeting → anonymous public feed
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass  # anonymous client — proceed with public data

    q = await broadcaster.register()
    tick = 0
    try:
        # initial burst: send the latest snapshot immediately
        if broadcaster.latest is not None:
            trimmed = _apply_tier(broadcaster.latest, tier, 0)
            if trimmed:
                await ws.send_text(json.dumps(trimmed, separators=(",", ":")))
        while True:
            snapshot = await q.get()
            tick += 1
            trimmed = _apply_tier(snapshot, tier, tick)
            if trimmed:
                await ws.send_text(json.dumps(trimmed, separators=(",", ":")))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("ws client error")
    finally:
        broadcaster.unregister(q)
        with contextlib.suppress(Exception):
            await ws.close()
