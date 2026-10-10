"""Base connector: resilient WebSocket lifecycle with exponential backoff.

Contract for concrete feeds:

* ``fetch_universe(n)``          -> top-n liquid perpetuals (REST, public)
* ``connect(top_symbols)``       -> async generator yielding raw messages
* ``handle_message(msg)``        -> parse raw message, invoke normalized callbacks
* ``subscribe_messages(syms)``   -> list of subscription payloads to send on connect

The base class owns reconnection (exponential backoff + jitter), a staleness
watchdog (forces reconnect if no message arrives within ``stale_timeout``),
and periodic subscription refresh when the tracked universe changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("scalpair.feed")

# ---------------------------------------------------------------------------
# Normalized event callback types
# ---------------------------------------------------------------------------
BookCallback = Callable[[str, list[tuple[float, float]], list[tuple[float, float]], float], Awaitable[None]]
TradeCallback = Callable[[str, float, float, float, bool], Awaitable[None]]  # sym, ts_ms, price, qty, is_buyer_maker
CandleCallback = Callable[[str, int, float, float, float, float, float], Awaitable[None]]  # sym, open_ms, o,h,l,c,v
TickerCallback = Callable[[str, float, float, float], Awaitable[None]]  # sym, last, change24h_pct, quote_vol_24h
MetaCallback = Callable[[str, dict], Awaitable[None]]  # sym, extra fields (funding, oi, ...)


@dataclass
class FeedCallbacks:
    on_book: BookCallback
    on_trade: TradeCallback
    on_candle: CandleCallback
    on_ticker: TickerCallback
    on_meta: MetaCallback


@dataclass
class FeedStats:
    connects: int = 0
    reconnects: int = 0
    last_connect_at: float = 0.0
    last_message_at: float = 0.0
    messages: int = 0
    errors: int = 0


class FeedConnector:
    """Abstract resilient feed. Subclasses implement parsing only."""

    name: str = "base"
    ws_url: str = ""
    keepalive_payload: dict[str, Any] | None = None  # e.g. Bybit {"op": "ping"}
    keepalive_interval: float = 20.0

    def __init__(
        self,
        callbacks: FeedCallbacks,
        *,
        stale_timeout: float = 15.0,
        backoff_base: float = 1.5,
        backoff_max: float = 60.0,
        ping_interval: float = 20.0,
    ) -> None:
        self.callbacks = callbacks
        self.stale_timeout = stale_timeout
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.ping_interval = ping_interval
        self.stats = FeedStats()
        self._ws = None  # live socket ref (used for resync requests)
        self.top_symbols: list[str] = []
        self._stop = asyncio.Event()
        self._universe_dirty = asyncio.Event()

    # ------------------------------------------------------------------
    # Interface to implement
    # ------------------------------------------------------------------
    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:
        """Return the top-n most liquid perpetual tickers."""
        raise NotImplementedError

    async def subscribe_messages(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Subscription payloads to send right after the socket opens."""
        raise NotImplementedError

    async def handle_message(self, msg: Any) -> None:
        """Parse one raw message and dispatch normalized callbacks."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def set_universe(self, symbols: list[str]) -> None:
        self.top_symbols = list(symbols)
        self._universe_dirty.set()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Run forever: connect, consume, reconnect with backoff."""
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.ping_interval,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=4096,
                    compression=None,
                ) as ws:
                    self._ws = ws
                    self.stats.connects += 1
                    self.stats.last_connect_at = time.time()
                    attempt = 0
                    log.info("[%s] connected to %s", self.name, self.ws_url)
                    await asyncio.sleep(0.25)  # let the socket warm up before subscribing
                    for payload in await self.subscribe_messages(self.top_symbols):
                        await ws.send(json.dumps(payload))
                    await self._consume_with_watchdog(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — feeds must never crash the loop
                self.stats.errors += 1
                attempt += 1
                delay = min(self.backoff_max, self.backoff_base ** min(attempt, 8) + random.uniform(0, 1))
                log.warning("[%s] %s — reconnecting in %.1fs (attempt %d)", self.name, exc, delay, attempt)
                self.stats.reconnects += 1
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def _consume_with_watchdog(self, ws: Any) -> None:
        """Consume messages; bail out (triggering reconnect) on staleness."""
        last = time.time()

        async def _reader() -> None:
            nonlocal last
            async for raw in ws:
                last = time.time()
                self.stats.messages += 1
                self.stats.last_message_at = last
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    await self.handle_message(msg)
                except Exception:  # noqa: BLE001 — a malformed frame must not kill the feed
                    log.exception("[%s] failed to handle message", self.name)

        reader = asyncio.create_task(_reader())
        stale = asyncio.create_task(self._stale_watchdog(lambda: last))
        pinger: asyncio.Task | None = None
        if self.keepalive_payload:
            async def _pinger() -> None:
                while True:
                    await asyncio.sleep(self.keepalive_interval)
                    with contextlib.suppress(Exception):
                        if hasattr(self, "_send_keepalive"):
                            await self._send_keepalive(ws)  # venue-specific (e.g. OKX text ping)
                        else:
                            await ws.send(json.dumps(self.keepalive_payload))  # type: ignore[arg-type]
            pinger = asyncio.create_task(_pinger())
        done, _ = await asyncio.wait(
            {reader, stale, asyncio.ensure_future(self._stop.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        reader.cancel()
        stale.cancel()
        if pinger:
            pinger.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        with contextlib.suppress(asyncio.CancelledError):
            await stale
        if pinger:
            with contextlib.suppress(asyncio.CancelledError):
                await pinger
        if not self._stop.is_set():
            with contextlib.suppress(Exception):
                await ws.close()

    async def _stale_watchdog(self, last_msg_getter: Callable[[], float]) -> None:
        while True:
            await asyncio.sleep(self.stale_timeout / 2)
            idle = time.time() - last_msg_getter()
            if idle > self.stale_timeout:
                log.warning("[%s] no messages for %.1fs — forcing reconnect", self.name, idle)
                raise ConnectionClosed(None, None)  # handled by run()

    # ------------------------------------------------------------------
    # Universe refresh helper (used by the orchestrator)
    # ------------------------------------------------------------------
    async def refresh_universe(self, n: int) -> list[str]:
        tickers = await self.fetch_universe(n)
        symbols = [t["symbol"] for t in tickers]
        if symbols != self.top_symbols:
            log.info("[%s] universe refreshed: %d symbols", self.name, len(symbols))
            self.set_universe(symbols)
        return symbols
