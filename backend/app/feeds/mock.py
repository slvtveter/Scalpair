"""Synthetic mock feed — offline bootstrap / demo / testing mode.

Generates plausible market microstructure: geometric random-walk prices,
Poisson trade flow with bursts, order books with occasional large walls,
1m candles, 24h tickers, funding & open-interest meta. Emits the same
normalized callbacks as the live connectors so the whole downstream
pipeline (analytics, ML scorer, API, frontend) runs identically offline.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any

from app.feeds.base import FeedCallbacks, FeedConnector

log = logging.getLogger("scalpair.feed.mock")

MOCK_UNIVERSE: list[dict[str, Any]] = [
    {"symbol": "BTCUSDT", "base": 68000.0, "tick": 0.1},
    {"symbol": "ETHUSDT", "base": 3500.0, "tick": 0.05},
    {"symbol": "SOLUSDT", "base": 160.0, "tick": 0.01},
    {"symbol": "BNBUSDT", "base": 590.0, "tick": 0.01},
    {"symbol": "XRPUSDT", "base": 0.52, "tick": 0.0001},
    {"symbol": "DOGEUSDT", "base": 0.14, "tick": 0.00001},
    {"symbol": "AVAXUSDT", "base": 28.0, "tick": 0.001},
    {"symbol": "LINKUSDT", "base": 14.5, "tick": 0.001},
    {"symbol": "APTUSDT", "base": 8.4, "tick": 0.001},
    {"symbol": "ARBUSDT", "base": 0.75, "tick": 0.0001},
    {"symbol": "OPUSDT", "base": 1.65, "tick": 0.0001},
    {"symbol": "NEARUSDT", "base": 5.1, "tick": 0.001},
]


class MockFeed(FeedConnector):
    name = "mock"

    def __init__(self, callbacks: FeedCallbacks, **kwargs: Any) -> None:
        super().__init__(callbacks, **kwargs)
        self._prices: dict[str, float] = {}
        self._ticks: dict[str, float] = {}
        self._burst: dict[str, float] = {m["symbol"]: 0.0 for m in MOCK_UNIVERSE}
        self._candle_start: dict[str, tuple[int, float, float, float, float, float]] = {}

    # ------------------------------------------------------------------
    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:
        return [
            {
                "symbol": m["symbol"],
                "last_price": m["base"],
                "quote_volume_24h": random.uniform(5e7, 4e9),
                "price_change_pct_24h": random.uniform(-9, 9),
            }
            for m in MOCK_UNIVERSE[: max(1, min(n, len(MOCK_UNIVERSE)))]
        ]

    async def subscribe_messages(self, symbols: list[str]) -> list[dict[str, Any]]:
        self.top_symbols = list(symbols)
        return []

    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Override: no real socket — synthesize events on a fixed cadence."""
        log.info("[mock] synthetic market starting with %d symbols", len(self.top_symbols))
        for sym in self.top_symbols:
            meta = next((m for m in MOCK_UNIVERSE if m["symbol"] == sym), MOCK_UNIVERSE[0])
            self._prices[sym] = meta["base"]
            self._ticks[sym] = meta["tick"]
        book_task = asyncio.create_task(self._book_loop())
        trade_task = asyncio.create_task(self._trade_loop())
        ticker_task = asyncio.create_task(self._ticker_loop())
        tasks = [book_task, trade_task, ticker_task]
        await asyncio.wait({*tasks, asyncio.ensure_future(self._stop.wait())},
                           return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()

    # ------------------------------------------------------------------
    async def _book_loop(self) -> None:
        while not self._stop.is_set():
            ts = time.time() * 1000
            for sym in self.top_symbols:
                price = self._prices.get(sym, 1.0)
                tick = self._ticks.get(sym, 0.01)
                spread = price * 0.0002
                median_level = price * 200  # a "normal" level ≈ price * 200 units
                bids, asks = [], []
                for i in range(1, 21):
                    lvl = price - spread / 2 - i * tick * 5
                    qty = median_level / lvl * random.uniform(0.4, 1.6)
                    # occasional bid wall
                    if random.random() < 0.012:
                        qty *= random.uniform(4, 12)
                    bids.append((round(lvl, 8), round(qty, 4)))
                for i in range(1, 21):
                    lvl = price + spread / 2 + i * tick * 5
                    qty = median_level / lvl * random.uniform(0.4, 1.6)
                    if random.random() < 0.012:
                        qty *= random.uniform(4, 12)
                    asks.append((round(lvl, 8), round(qty, 4)))
                await self.callbacks.on_book(sym, bids, asks, ts)
            await asyncio.sleep(0.25)

    async def _trade_loop(self) -> None:
        while not self._stop.is_set():
            ts = time.time() * 1000
            n_events = random.randint(3, 12)
            for sym in random.choices(self.top_symbols, k=n_events):
                price = self._prices.get(sym, 1.0)
                tick = self._ticks.get(sym, 0.01)
                # evolve price with drift + bursts
                burst = self._burst.get(sym, 0.0)
                drift = random.gauss(burst * 0.0002, 0.0004)
                self._prices[sym] = max(tick, price * (1 + drift))
                self._burst[sym] = burst * 0.9 + (random.uniform(0.5, 2.0) if random.random() < 0.01 else 0)
                direction = 1 if drift >= 0 else -1
                qty = abs(random.gauss(0, 1)) * 40 + 0.5
                if random.random() < 0.02:  # whale print
                    qty *= random.uniform(20, 60)
                await self.callbacks.on_trade(
                    sym, ts, round(self._prices[sym], 8), round(qty, 4), is_buyer_maker=(direction < 0)
                )
                # feed the 1m candle pipeline
                await self._emit_candle_tick(sym, ts, self._prices[sym], qty)
            await asyncio.sleep(0.2)

    async def _emit_candle_tick(self, sym: str, ts_ms: float, price: float, qty: float) -> None:
        bucket = int(ts_ms // 60_000) * 60_000
        cur = self._candle_start.get(sym)
        if cur is None or cur[0] != bucket:
            cur = self._candle_start[sym] = (bucket, price, price, price, price, 0.0)
            await self.callbacks.on_candle(sym, bucket, price, price, price, price, qty)
        else:
            o, h, l, c, v = cur[1], max(cur[2], price), min(cur[3], price), price, cur[5] + qty
            self._candle_start[sym] = (bucket, o, h, l, c, v)
            await self.callbacks.on_candle(sym, bucket, o, h, l, c, v)

    async def _ticker_loop(self) -> None:
        changes = {m["symbol"]: random.uniform(-6, 6) for m in MOCK_UNIVERSE}
        vols = {m["symbol"]: random.uniform(1e8, 2e9) for m in MOCK_UNIVERSE}
        while not self._stop.is_set():
            ts = time.time() * 1000
            for sym in self.top_symbols:
                price = self._prices.get(sym, 1.0)
                await self.callbacks.on_ticker(sym, price, changes.get(sym, 0.0), vols.get(sym, 1e8))
                if random.random() < 0.3:
                    await self.callbacks.on_meta(sym, {"funding_rate": random.gauss(0.0001, 0.0004)})
                if random.random() < 0.1:
                    await self.callbacks.on_meta(sym, {"open_interest": price * random.uniform(1e5, 5e6)})
            await asyncio.sleep(3)
