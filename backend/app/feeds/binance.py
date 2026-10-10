"""Binance USDⓈ-M Futures public feed (primary).

Streams (all unauthenticated):
* ``<sym>@depth20@100ms``  partial L2 book snapshots (top 20 levels each side)
* ``<sym>@aggTrade``       aggregated trades
* ``<sym>@kline_1m``       1-minute candles (seeds BB-width pipeline)
* ``!ticker@arr``          rolling 24h stats for all markets (universe + change%)

Docs: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.feeds.base import FeedConnector

log = logging.getLogger("scalpair.feed.binance")

REST_BASE = "https://fapi.binance.com"
WS_URL = "wss://fstream.binance.com/stream"

# tokenized stocks/commodities pollute a scalping universe (review finding):
# exclude explicitly plus a 24h liquidity floor.
NON_CRYPTO_SYMBOLS = {
    "XAUUSDT", "XAGUSDT", "SOXLUSDT", "SPCXUSDT", "KORUUSDT", "USUSDT", "BZUSDT",
    "CLUSDT", "NGUSDT", "SNDKUSDT", "GIGAUSDT", "SOLVUSDT", "BIDUSDT", "ZROUSDT",
}
MIN_QUOTE_VOLUME_24H = 20_000_000.0


class BinanceFuturesFeed(FeedConnector):
    name = "binance"
    ws_url = WS_URL

    # ------------------------------------------------------------------
    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:
        url = f"{REST_BASE}/fapi/v1/ticker/24hr"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
        tickers = []
        for row in data:
            sym = row.get("symbol", "")
            if not sym.endswith("USDT") or "_" in sym:
                continue  # USDT-margined only, skip e.g. BTCUSDT_250627
            if sym in NON_CRYPTO_SYMBOLS:
                continue
            try:
                tickers.append(
                    {
                        "symbol": sym,
                        "last_price": float(row["lastPrice"]),
                        "quote_volume_24h": float(row["quoteVolume"]),
                        "price_change_pct_24h": float(row["priceChangePercent"]),
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
        tickers = [t for t in tickers if t["quote_volume_24h"] >= MIN_QUOTE_VOLUME_24H]
        tickers.sort(key=lambda t: t["quote_volume_24h"], reverse=True)
        return tickers[:n]

    async def fetch_klines(self, symbol: str, limit: int = 300) -> list[tuple]:
        """Backfill 1m OHLCV via public REST: [(open_ms, o, h, l, c, v)]."""
        url = f"{REST_BASE}/fapi/v1/klines"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, params={"symbol": symbol, "interval": "1m", "limit": limit}) as resp:
                resp.raise_for_status()
                rows = await resp.json()
        out = []
        for r in rows:
            try:
                out.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
            except (IndexError, ValueError, TypeError):
                continue
        return out

    # ------------------------------------------------------------------
    async def subscribe_messages(self, symbols: list[str]) -> list[dict[str, Any]]:
        streams = ["!ticker@arr"]
        for s in symbols:
            low = s.lower()
            streams += [f"{low}@depth20@100ms", f"{low}@aggTrade", f"{low}@kline_1m"]
        # chunked SUBSCRIBE to stay well under frame size limits
        msgs = []
        for i in range(0, len(streams), 100):
            msgs.append({"method": "SUBSCRIBE", "params": streams[i : i + 100], "id": i + 1})
        return msgs

    # ------------------------------------------------------------------
    async def handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        # combined stream wrapper: {"stream": "...", "data": {...}}
        stream = msg.get("stream")
        data = msg.get("data", msg)
        if isinstance(data, list):  # !ticker@arr
            await self._on_ticker_arr(data)
            return
        if not isinstance(data, dict):
            return
        event = data.get("e")
        if event == "depthUpdate":
            await self._on_depth(data)
        elif event == "aggTrade":
            await self._on_agg_trade(data)
        elif event == "kline":
            await self._on_kline(data)

    # ------------------------------------------------------------------
    async def _on_ticker_arr(self, arr: list[dict[str, Any]]) -> None:
        for row in arr:
            sym = row.get("s", "")
            if sym not in {s.upper() for s in self.top_symbols} and not self._is_tracked(sym):
                continue
            try:
                await self.callbacks.on_ticker(
                    sym,
                    float(row.get("c", 0.0)),
                    float(row.get("P", 0.0)),
                    float(row.get("q", 0.0)),
                )
            except (ValueError, TypeError):
                continue

    def _is_tracked(self, symbol: str) -> bool:
        return symbol in self.top_symbols

    async def _on_depth(self, data: dict[str, Any]) -> None:
        sym = data.get("s", "")
        if not self._is_tracked(sym):
            return
        try:
            bids = [(float(p), float(q)) for p, q in data.get("b", []) if float(q) > 0]
            asks = [(float(p), float(q)) for p, q in data.get("a", []) if float(q) > 0]
        except (ValueError, TypeError):
            return
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        await self.callbacks.on_book(sym, bids, asks, float(data.get("E", 0) or 0))

    async def _on_agg_trade(self, data: dict[str, Any]) -> None:
        sym = data.get("s", "")
        if not self._is_tracked(sym):
            return
        try:
            await self.callbacks.on_trade(
                sym,
                float(data["T"]),
                float(data["p"]),
                float(data["q"]),
                bool(data.get("m", False)),
            )
        except (KeyError, ValueError, TypeError):
            return

    async def _on_kline(self, data: dict[str, Any]) -> None:
        sym = data.get("s", "")
        if not self._is_tracked(sym):
            return
        k = data.get("k", {})
        try:
            await self.callbacks.on_candle(
                sym,
                int(k["t"]),
                float(k["o"]),
                float(k["h"]),
                float(k["l"]),
                float(k["c"]),
                float(k["v"]),
            )
        except (KeyError, ValueError, TypeError):
            return
