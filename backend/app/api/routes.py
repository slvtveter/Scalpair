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
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.api.auth import User, current_user
from app.instruments import market_for_feed, venue_for_feed
from app.config import get_settings
from app.models import HealthReport
from app.history import TIMEFRAMES, aggregate_history, normalize_rows
from app.feeds.base import FeedCallbacks
from app.feeds.binance import BinanceFuturesFeed, BinanceSpotFeed
from app.feeds.bybit import BybitLinearFeed, BybitSpotFeed
from app.feeds.okx import OkxSwapFeed
from app.feeds.mock import MockFeed

log = logging.getLogger("scalpair.api")

router = APIRouter(prefix="/api/v1", tags=["screeners"])

START_TIME = time.time()

FREE_TIER_SYMBOLS = 10
FREE_TIER_UPDATE_DIVISOR = 4  # ~15s effective delay at 0.5Hz metrics cadence


def _tracked_count(streamer: Any) -> int:
    if streamer is None:
        return 0
    if getattr(streamer, "multi_mode", False):
        return sum(len(symbols) for symbols in streamer.source_symbols.values())
    return len(streamer.top_symbols)


async def _noop_feed_callback(*args: Any) -> None:
    return None


def _history_source_for_venue(venue: str):
    callbacks = FeedCallbacks(_noop_feed_callback, _noop_feed_callback, _noop_feed_callback, _noop_feed_callback, _noop_feed_callback)
    return {
        "BI-F": BinanceFuturesFeed(callbacks),
        "BI-S": BinanceSpotFeed(callbacks),
        "BY-F": BybitLinearFeed(callbacks),
        "BY-S": BybitSpotFeed(callbacks),
        "OK-F": OkxSwapFeed(callbacks),
        "DEMO": MockFeed(callbacks),
    }.get(venue)


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
    feed_stats = {}
    if streamer and streamer.feed:
        stats = streamer.feed.stats
        feed_stats = {"connects": stats.connects, "reconnects": stats.reconnects, "messages": stats.messages,
                      "errors": stats.errors, "last_error": stats.last_error}
    uptime = time.time() - START_TIME
    # A connector can pass the universe probe and still fail to ingest WS
    # events. After a short warmup that state is degraded, never healthy.
    no_ingest_after_warmup = state.messages_ingested == 0 and uptime > 30
    no_websocket_after_warmup = bool(
        streamer and streamer.feed and streamer.feed.stats.connects == 0 and uptime > 10
    )
    return HealthReport(
        status="ok" if not streamer.feed_degraded and not no_ingest_after_warmup and not no_websocket_after_warmup and (last_age is None or last_age < 30_000) else "degraded",
        uptime_s=round(uptime, 1),
        active_feeds=feeds,
        active_ws_clients=broadcaster.client_count,
        tracked_symbols=_tracked_count(streamer),
        messages_ingested=state.messages_ingested,
        avg_ingest_latency_ms=round(state.ingest_latency_ema_ms, 2),
        last_message_age_ms=round(last_age) if last_age is not None else None,
        redis_connected=broadcaster.redis_connected,
        feed_mode=state.feed_mode,
        feed_stats=feed_stats,
    )


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Process readiness, separate from upstream market freshness."""
    streamer = request.app.state.streamer
    if streamer is None or not streamer.top_symbols:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "market streamer is not initialized")
    ws_unavailable = bool(
        streamer.feed and streamer.feed.stats.connects == 0 and time.time() - START_TIME > 10
    )
    return JSONResponse(
        {
            "ready": True,
            "feed": request.app.state.market_state.feed_mode,
            "tracked_symbols": _tracked_count(streamer),
            "market_status": "degraded" if streamer.feed_degraded or ws_unavailable else "live",
        }
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    """Small Prometheus-compatible surface for Render/ops monitoring."""
    state = request.app.state.market_state
    streamer = request.app.state.streamer
    feed = streamer.feed if streamer else None
    stats = feed.stats if feed else None
    values = {
        "scalpair_messages_ingested_total": state.messages_ingested,
        "scalpair_tracked_symbols": _tracked_count(streamer),
        "scalpair_active_ws_clients": request.app.state.broadcaster.client_count,
        "scalpair_feed_degraded": int(bool(streamer and streamer.feed_degraded)),
        "scalpair_feed_messages_total": stats.messages if stats else 0,
        "scalpair_feed_reconnects_total": stats.reconnects if stats else 0,
        "scalpair_feed_errors_total": stats.errors if stats else 0,
    }
    body = "\n".join(f"{name} {value}" for name, value in values.items()) + "\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


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
                    "market": market_for_feed(feed_name),
                    "side": w["side"],
                    "price": w["price"],
                    "notional_usd": w["notional_usd"],
                    "qty": w["qty"],
                    "distance_pct": w["distance_pct"],
                    "seconds_to_eat": w.get("seconds_to_eat"),
                    "detected_at": w["detected_at"],
                    "age_s": w.get("age_s", 0.0),
                    "book_ts": st.book_ts_ms,
                    "brp_usd": st.density_brp_usd,
                    "category": w.get("category"),
                    "levels_bid": st.density_bid_levels,
                    "levels_ask": st.density_ask_levels,
                }
            )
    walls.sort(key=lambda w: w["notional_usd"], reverse=True)
    return JSONResponse({
        "ts": int(time.time() * 1000),
        "walls": walls[:limit],
        "total": len(walls),
        "feed": state.feed_mode,
        "coverage": {
            "kind": "top_n_levels",
            "max_levels_per_side": max((max((w["levels_bid"] for w in walls), default=0), max((w["levels_ask"] for w in walls), default=0))),
            "complete_percent_range": False,
            "note": "Only received top-N L2 levels; this is not full 1–10% market depth.",
        },
    })


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
    # Reuse the snapshot produced by the metrics loop. Rebuilding all walls,
    # levels and picks in the request path can starve the event loop on a free
    # Render instance while the upstream feed is busy.
    if streamer._last_overview_body is None:
        streamer.build_snapshot()
    if streamer._last_overview_body is not None:
        return Response(content=streamer._last_overview_body, media_type="application/json")
    return JSONResponse({"ts": int(time.time() * 1000), "symbols": []})


@router.get("/screeners/score-alerts")
async def recent_alerts(request: Request) -> JSONResponse:
    streamer = request.app.state.streamer
    return JSONResponse({"ts": int(time.time() * 1000), "alerts": streamer.alerts[-25:]})


@router.get("/markets/symbols")
async def symbols(request: Request) -> JSONResponse:
    streamer = request.app.state.streamer
    feed_name = streamer.feed.name if streamer.feed else ""
    venue = venue_for_feed(feed_name)
    market = market_for_feed(feed_name)
    return JSONResponse(
        {
            "feed": request.app.state.market_state.feed_mode,
            "symbols": [
                {
                    "symbol": s,
                    "instrument_id": f"{venue}:{market}:{s}",
                    "price": request.app.state.market_state.symbols[s].price,
                    "quote_volume_24h": request.app.state.market_state.symbols[s].quote_volume_24h,
                    "asset_id": request.app.state.market_state.symbols[s].asset_id,
                    "market_cap_usd": request.app.state.market_state.symbols[s].market_cap_usd,
                    "market_cap_source": request.app.state.market_state.symbols[s].market_cap_source,
                    "market_cap_updated_at": request.app.state.market_state.symbols[s].market_cap_updated_at,
                    "venue": venue,
                    "market": market,
                }
                for s in streamer.top_symbols if s in request.app.state.market_state.symbols
            ],
        }
    )


@router.get("/instruments")
async def instruments(
    request: Request,
    q: str | None = Query(default=None, max_length=64),
    exchange: str | None = Query(default=None, max_length=16),
    market: str | None = Query(default=None, max_length=16),
    all_sources: bool = Query(default=False),
    source: str | None = Query(default=None, max_length=8),
) -> JSONResponse:
    """Source-labelled instrument discovery catalog.

    ``all_sources`` uses REST ticker snapshots for discovery only. Rows retain
    their exact source identity and are not presented as a live unified feed.
    """
    streamer = request.app.state.streamer
    query = q.strip().upper() if q else ""
    exchange_filter = exchange.strip().upper() if exchange else ""
    market_filter = market.strip().upper() if market else ""
    source_filter = source.strip().upper() if source else ""
    source_map = {
        "BI-F": BinanceFuturesFeed, "BI-S": BinanceSpotFeed,
        "BY-F": BybitLinearFeed, "BY-S": BybitSpotFeed,
        "OK-F": OkxSwapFeed, "DEMO": MockFeed,
    }
    callbacks = FeedCallbacks(_noop_feed_callback, _noop_feed_callback, _noop_feed_callback, _noop_feed_callback, _noop_feed_callback)
    active_venue = venue_for_feed(streamer.feed.name) if streamer.feed else ""
    if source_filter in source_map:
        requested = [streamer.feed] if streamer.feed and active_venue == source_filter else [source_map[source_filter](callbacks)]
    elif all_sources:
        requested = ([streamer.feed] if streamer.feed else []) + [
            cls(callbacks) for venue, cls in source_map.items()
            if venue != "DEMO" and venue != active_venue
        ]
    else:
        requested = [streamer.feed] if streamer.feed else []

    async def rows_for(feed):
        if feed is streamer.feed:
            return [(sym, request.app.state.market_state.symbols.get(sym)) for sym in streamer.top_symbols]
        try:
            tickers = await asyncio.wait_for(feed.fetch_universe(streamer.settings.top_symbols), timeout=5)
            return [(row.get("symbol"), row) for row in tickers]
        except Exception:
            return []

    rows = []
    feed_rows = await asyncio.gather(*(rows_for(feed) for feed in requested if feed is not None))
    for feed, source_rows in zip([feed for feed in requested if feed is not None], feed_rows):
        venue = venue_for_feed(feed.name)
        feed_market = market_for_feed(feed.name)
        if exchange_filter and exchange_filter not in venue:
            continue
        if market_filter and market_filter != feed_market:
            continue
        for symbol, raw in source_rows:
            if not symbol:
                continue
            symbol = str(symbol).upper()
            if query and query not in symbol:
                continue
            if isinstance(raw, dict):
                price = float(raw.get("last_price") or 0)
                volume = float(raw.get("quote_volume_24h") or 0)
                state_row = request.app.state.market_state.symbols.get(symbol)
            else:
                price = float(raw.price) if raw else 0
                volume = float(raw.quote_volume_24h) if raw else 0
                state_row = raw
            rows.append({
                "asset_id": (state_row.asset_id if state_row else None) or symbol.removesuffix("USDT").lower(),
                "symbol": symbol,
                "instrument_id": f"{venue}:{feed_market}:{symbol}",
                "venue": venue, "exchange": venue.split("-", 1)[0], "market": feed_market,
                "quote": "USDT", "price": price, "quote_volume_24h": volume,
                "market_cap_usd": state_row.market_cap_usd if state_row else None,
                "market_cap_source": state_row.market_cap_source if state_row else None,
                "market_cap_updated_at": state_row.market_cap_updated_at if state_row else None,
                "live": feed is streamer.feed,
            })
    return JSONResponse({"feed": request.app.state.market_state.feed_mode, "all_sources": all_sources, "instruments": rows})


@router.get("/markets/{symbol}/candles")
async def candles(
    request: Request,
    symbol: str,
    limit: int = Query(300, ge=10, le=500),
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D"] = "1m",
    instrument_id: str | None = None,
) -> JSONResponse:
    """1-minute OHLCV candles + current walls for the chart view."""
    state = request.app.state.market_state
    st = state.symbols.get(symbol.upper())
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown symbol {symbol}")
    streamer = request.app.state.streamer
    feed, generation = streamer.feed, streamer._generation
    active_venue = venue_for_feed(feed.name if feed else "")
    active_market = market_for_feed(feed.name if feed else "")
    venue = active_venue
    market = active_market
    expected_instrument = f"{venue}:{market}:{st.symbol}"
    selected_feed = feed
    alternate_source = False
    if instrument_id and instrument_id != expected_instrument:
        pieces = instrument_id.split(":")
        if len(pieces) != 3 or pieces[1] not in {"FUTURES", "SPOT"} or pieces[2].upper() != st.symbol:
            raise HTTPException(status.HTTP_409_CONFLICT, "Instrument source changed; retry with the active instrument")
        selected_feed = _history_source_for_venue(pieces[0])
        if selected_feed is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported history source")
        if pieces[1] != market_for_feed(selected_feed.name):
            raise HTTPException(status.HTTP_409_CONFLICT, "Instrument market does not match the selected source")
        venue = pieces[0]
        market = pieces[1]
        alternate_source = True
    source = "stream"
    history = aggregate_history(list(st.candles), TIMEFRAMES[timeframe])[-limit:]
    limited = timeframe != "1m"
    if (timeframe != "1m" or alternate_source) and selected_feed is not None and hasattr(selected_feed, "fetch_klines"):
        try:
            if alternate_source:
                rows = await selected_feed.fetch_klines(st.symbol, limit=limit, timeframe=timeframe)
                history = normalize_rows([dict(t=t, o=o, h=h, l=l, c=c, v=v) for t, o, h, l, c, v in rows])
            else:
                history = await streamer.history.get(generation, selected_feed, st.symbol, timeframe, limit)
        except Exception as exc:
            raise HTTPException(503, "Exchange history temporarily unavailable") from exc
        if not alternate_source and (generation != streamer._generation or feed is not streamer.feed):
            raise HTTPException(409, "Market source changed; retry history request")
        source = "exchange_rest"
        limited = False
    return JSONResponse(
        {
            "symbol": st.symbol,
            "instrument_id": f"{venue}:{market}:{st.symbol}",
            "timeframe": timeframe,
            "feed": state.feed_mode,
            "venue": venue,
            "market": market,
            "sourceGeneration": generation,
            "historySource": source,
            "overlaysSource": "active_live_feed" if not alternate_source else "unavailable_for_selected_source",
            "overlaysAvailable": not alternate_source,
            "limitedHistory": limited,
            "levelTimeframe": "1m",
            "candles": history,
            "walls": [] if alternate_source else [
                {
                    "side": w["side"],
                    "price": w["price"],
                    "notional": w["notional_usd"],
                    "age_s": w.get("age_s", 0.0),
                }
                for w in sorted(st.walls.values(), key=lambda w: w["notional_usd"], reverse=True)[:6]
            ],
            "levels": [] if alternate_source else [
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
