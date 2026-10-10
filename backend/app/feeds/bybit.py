"""Bybit v5 public linear feed (fallback when Binance is unreachable).

Streams (all unauthenticated):
* ``orderbook.50.<SYM>``  snapshot + delta L2 (top 50 levels each side)
* ``publicTrade.<SYM>``   public trades (S = taker side)
* ``kline.1m.<SYM>``      1-minute candles
* ``tickers.<SYM>``       24h stats + funding rate + open interest

Docs: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import aiohttp

from app.feeds.base import FeedConnector

log = logging.getLogger("scalpair.feed.bybit")

REST_BASE = "https://api.bybit.com"
WS_URL = "wss://stream.bybit.com/v5/public/linear"


class BybitLinearFeed(FeedConnector):
    name = "bybit"
    ws_url = WS_URL
    keepalive_payload = {"op": "ping"}
    keepalive_interval = 20.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # maintained L2 books: symbol -> {"bids": {price: qty}, "asks": {price: qty}}
        self._books: dict[str, dict[str, dict[float, float]]] = {}
        # last known ticker values for delta-frame merging
        self._ticker_cache: dict[str, tuple[float, float, float]] = {}
        self._book_u: dict[str, int] = {}  # last update id per symbol
        self._resync_pending: set[str] = set()  # symbols awaiting a fresh snapshot

    # ------------------------------------------------------------------
    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:
        url = f"{REST_BASE}/v5/market/tickers"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, params={"category": "linear"}) as resp:
                resp.raise_for_status()
                body = await resp.json()
        if body.get("retCode", 0) != 0:
            raise ValueError("Bybit history request rejected")
        rows = (body.get("result") or {}).get("list") or []
        tickers = []
        for row in rows:
            sym = row.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                tickers.append(
                    {
                        "symbol": sym,
                        "last_price": float(row["lastPrice"]),
                        "quote_volume_24h": float(row["turnover24h"]),
                        "price_change_pct_24h": float(row["price24hPcnt"]) * 100.0,
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
        tickers.sort(key=lambda t: t["quote_volume_24h"], reverse=True)
        return tickers[:n]

    async def fetch_klines(self, symbol: str, limit: int = 300, timeframe: str = "1m") -> list[tuple]:
        """Backfill 1m OHLCV via public REST (Bybit interval "1"): [(open_ms, o, h, l, c, v)]."""
        url = f"{REST_BASE}/v5/market/kline"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(
                url,
                params={"category": "linear", "symbol": symbol, "interval": {"1m":"1", "3m":"3", "5m":"5", "15m":"15", "30m":"30", "1h":"60", "4h":"240", "1D":"D"}[timeframe], "limit": min(limit, 1000)},
            ) as resp:
                resp.raise_for_status()
                body = await resp.json()
        rows = (body.get("result") or {}).get("list") or []
        out = []
        for r in rows:  # newest-first: [start, open, high, low, close, volume, turnover]
            try:
                out.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
            except (IndexError, ValueError, TypeError):
                continue
        out.reverse()  # oldest-first to match Binance ordering
        return out

    # ------------------------------------------------------------------
    async def subscribe_messages(self, symbols: list[str]) -> list[dict[str, Any]]:
        args = []
        for s in symbols:
            # NB: kline interval is "1" (not "1m"); a single invalid topic in a
            # subscribe batch causes Bybit to reject the *entire* batch.
            args += [f"orderbook.50.{s}", f"publicTrade.{s}", f"kline.1.{s}", f"tickers.{s}"]
        msgs = []
        for i in range(0, len(args), 10):  # Bybit allows 10 args per subscribe
            msgs.append({"op": "subscribe", "args": args[i : i + 10]})
        return msgs

    # ------------------------------------------------------------------
    async def handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or data is None:
            return  # ack / pong / error frames
        if topic.startswith("orderbook."):
            await self._on_orderbook(topic.split(".")[-1], msg.get("type", ""), data, msg.get("ts", 0))
        elif topic.startswith("publicTrade."):
            await self._on_public_trade(topic.split(".")[-1], data)
        elif topic.startswith("kline."):
            await self._on_kline(topic.split(".")[-1], data)
        elif topic.startswith("tickers."):
            await self._on_ticker(topic.split(".")[-1], data)

    # ------------------------------------------------------------------
    async def _on_orderbook(self, sym: str, mtype: str, data: dict[str, Any], ts: Any) -> None:
        if not self._is_tracked(sym) or not isinstance(data, dict):
            return
        book = self._books.setdefault(sym, {"bids": {}, "asks": {}})
        if mtype == "snapshot":
            book["bids"] = {}
            book["asks"] = {}
        try:
            for p, q in data.get("b", []):
                pf, qf = float(p), float(q)
                if qf <= 0:
                    book["bids"].pop(pf, None)
                else:
                    book["bids"][pf] = qf
            for p, q in data.get("a", []):
                pf, qf = float(p), float(q)
                if qf <= 0:
                    book["asks"].pop(pf, None)
                else:
                    book["asks"][pf] = qf
        except (ValueError, TypeError):
            return
        # Update-id continuity (spec: gap => data untrusted until snapshot resync).
        # Per-symbol `u`: snapshot carries the last update id, each delta must be
        # exactly prev_u + 1. (`seq` is connection-global and NOT per-symbol —
        # the earlier per-symbol seq check caused a false-positive resync storm.)
        u = data.get("u")
        if u is not None:
            try:
                u = int(u)
            except (TypeError, ValueError):
                u = None
        if u is not None and mtype == "delta" and sym in self._book_u and u != self._book_u[sym] + 1:
            log.warning(
                "orderbook update-id gap for %s (%d -> %d) — book stale until snapshot",
                sym, self._book_u[sym], u,
            )
            self._book_u.pop(sym, None)
            book["bids"] = {}
            book["asks"] = {}
            if sym not in self._resync_pending:
                self._resync_pending.add(sym)
                asyncio.create_task(self._resync_book(sym))
            return  # never push gapped/untrusted deltas downstream
        if mtype == "snapshot" and sym in self._resync_pending:
            self._resync_pending.discard(sym)
        if u is not None:
            self._book_u[sym] = u

        bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)[:25]
        asks = sorted(book["asks"].items(), key=lambda x: x[0])[:25]
        await self.callbacks.on_book(sym, bids, asks, float(ts or 0))

    async def _resync_book(self, sym: str) -> None:
        """Debounced unsubscribe+subscribe to force a fresh orderbook snapshot."""
        await asyncio.sleep(1.5)
        if self._stop.is_set() or self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"op": "unsubscribe", "args": [f"orderbook.50.{sym}"]}))
            await asyncio.sleep(0.3)
            await self._ws.send(json.dumps({"op": "subscribe", "args": [f"orderbook.50.{sym}"]}))
            log.info("orderbook resync requested for %s", sym)
        except Exception:  # noqa: BLE001 — connection loss is handled by the reconnect loop
            pass

    async def _on_public_trade(self, sym: str, data: Any) -> None:
        if not self._is_tracked(sym) or not isinstance(data, list):
            return
        for t in data:
            try:
                # S is the *taker* side: "Buy" => taker bought => buyer is taker (not maker)
                taker_buy = t.get("S") == "Buy"
                await self.callbacks.on_trade(sym, float(t["T"]), float(t["p"]), float(t["v"]), not taker_buy)
            except (KeyError, ValueError, TypeError):
                continue

    async def _on_kline(self, sym: str, data: Any) -> None:
        if not self._is_tracked(sym) or not isinstance(data, list):
            return
        for row in data:
            try:
                if isinstance(row, dict):
                    # observed live format: {"start": ms, "open": "...", ..., "volume": "..."}
                    await self.callbacks.on_candle(
                        sym,
                        int(row["start"]),
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                    )
                else:  # legacy list format: [start, open, high, low, close, volume, turnover]
                    await self.callbacks.on_candle(
                        sym,
                        int(row[0]),
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                    )
            except (KeyError, IndexError, ValueError, TypeError):
                continue

    async def _on_ticker(self, sym: str, data: dict[str, Any]) -> None:
        if not self._is_tracked(sym) or not isinstance(data, dict):
            return
        try:
            # delta frames carry only the changed fields — merge over the
            # last known values instead of overwriting them with zeros
            cached = self._ticker_cache.get(sym, (0.0, 0.0, 0.0))
            last = float(data["lastPrice"]) if data.get("lastPrice") is not None else cached[0]
            change = float(data["price24hPcnt"]) * 100.0 if data.get("price24hPcnt") is not None else cached[1]
            vol = float(data["turnover24h"]) if data.get("turnover24h") is not None else cached[2]
            self._ticker_cache[sym] = (last, change, vol)
            await self.callbacks.on_ticker(sym, last, change, vol)
            meta: dict[str, float] = {}
            if data.get("fundingRate") is not None:
                meta["funding_rate"] = float(data["fundingRate"])
            if data.get("openInterest") is not None:
                meta["open_interest"] = float(data["openInterest"])
            if meta:
                await self.callbacks.on_meta(sym, meta)
        except (ValueError, TypeError):
            return

    def _is_tracked(self, symbol: str) -> bool:
        return symbol in self.top_symbols
