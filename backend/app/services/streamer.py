"""MarketStreamer — orchestrates feeds, state, analytics and scoring.

Responsibilities:
* Select a feed (Binance → Bybit → mock) based on reachability or config.
* Feed the normalized callbacks into the shared MarketState.
* Maintain the tracked universe (top-N liquid USDT perps, refreshed every 60s).
* Tick loops: walls + metrics every 0.5s, AI scoring every `ml_score_interval`,
  publishing each result to the Broadcaster as a unified snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from app import analytics
from app.history import HistoryCache
from app.instruments import market_for_feed, venue_for_feed
from app.market_cap import MarketCapService
from app.config import Settings
from app.feeds.base import FeedCallbacks, FeedConnector
from app.feeds.binance import BinanceFuturesFeed, BinanceSpotFeed
from app.feeds.bybit import BybitLinearFeed, BybitSpotFeed
from app.feeds.okx import OkxSwapFeed
from app.feeds.mock import MockFeed
from app.ml_engine import ScalpScorer
from app.state import MarketState

log = logging.getLogger("scalpair.streamer")

METRICS_TICK_S = 0.25  # 4 Hz snapshot publish; WS_BROADCAST_RATE caps per-client delivery
UNIVERSE_REFRESH_S = 60.0
FEED_PROBE_TIMEOUT_S = 10.0
REST_FALLBACK_INTERVAL_S = 10.0


class MarketStreamer:
    def __init__(
        self,
        state: MarketState,
        settings: Settings,
        scorer: ScalpScorer,
        publish: Any,  # Broadcaster.publish
    ) -> None:
        self.state = state
        self.settings = settings
        self.scorer = scorer
        self.publish = publish
        self.feed: FeedConnector | None = None
        self.extra_feeds: list[FeedConnector] = []  # supplementary feeds (hybrid mode)
        self.multi_mode = False
        self.source_states: dict[str, MarketState] = {}
        self.source_symbols: dict[str, list[str]] = {}
        self._last_snapshot: dict[str, Any] | None = None
        self._last_overview_body: bytes | None = None
        self.top_symbols: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.last_scoring_at = 0.0
        self.alerts: list[dict[str, Any]] = []  # recent high-score alert log
        self._fired_alerts: dict[str, float] = {}
        # per-event-type counters for degradation detection
        self.book_events = 0
        self.trade_events = 0
        self.history = HistoryCache()
        self.hybrid_mode = False
        self.feed_degraded = False
        self._generation = 0
        self._seeded_symbols: set[str] = set()
        self._backfill_lock = asyncio.Lock()
        self._failover_lock = asyncio.Lock()
        self.market_caps = MarketCapService(
            settings.market_cap_url,
            settings.market_cap_refresh_s,
            settings.market_cap_enabled,
        )

    # ------------------------------------------------------------------
    # Callbacks (feed → state)
    # ------------------------------------------------------------------
    def _make_callbacks(self, target_state: MarketState | None = None) -> FeedCallbacks:
        state = target_state or self.state
        generation = self._generation

        async def on_book(sym, bids, asks, ts_ms):
            if generation != self._generation:
                return
            state.get(sym).on_book(bids, asks, ts_ms or time.time() * 1000)
            state.record_ingest()
            self.book_events += 1

        async def on_trade(sym, ts_ms, price, qty, is_buyer_maker):
            if generation != self._generation:
                return
            state.get(sym).on_trade(ts_ms or time.time() * 1000, price, qty, is_buyer_maker)
            state.record_ingest()
            self.trade_events += 1

        async def on_candle(sym, open_ms, o, h, l, c, v):
            if generation != self._generation:
                return
            state.get(sym).on_candle(open_ms, o, h, l, c, v)
            state.record_ingest()

        async def on_ticker(sym, last, change_pct, quote_vol):
            if generation != self._generation:
                return
            state.get(sym).on_ticker_24h(last, change_pct, quote_vol)
            state.record_ingest()

        async def on_meta(sym, meta):
            if generation != self._generation:
                return
            st = state.get(sym)
            if "funding_rate" in meta:
                st.funding_rate = float(meta["funding_rate"])
            if "open_interest" in meta:
                # raw OI is base units - convert with the live price
                st.open_interest_usd = float(meta["open_interest"]) * (st.price or 0.0)
            state.record_ingest()

        return FeedCallbacks(on_book, on_trade, on_candle, on_ticker, on_meta)

    # ------------------------------------------------------------------
    # Feed selection
    # ------------------------------------------------------------------
    def _candidate_feeds(self) -> list[FeedConnector]:
        callbacks = self._make_callbacks()
        mode = self.settings.data_feed
        binance = BinanceFuturesFeed(callbacks)
        bybit = BybitLinearFeed(callbacks)
        if mode == "binance":
            return [binance]
        if mode == "binance_spot":
            return [BinanceSpotFeed(callbacks)]
        if mode == "bybit":
            return [bybit]
        if mode == "bybit_spot":
            return [BybitSpotFeed(callbacks)]
        if mode == "okx":
            return [OkxSwapFeed(callbacks)]
        if mode == "mock":
            return [MockFeed(callbacks)]
        if mode == "multi":
            return [binance, bybit, BinanceSpotFeed(callbacks), BybitSpotFeed(callbacks), OkxSwapFeed(callbacks)]
        # Prefer OKX before Bybit: some Render regions receive HTTP 403 from
        # Bybit's public websocket edge even though its REST probe succeeds.
        return [binance, OkxSwapFeed(callbacks), bybit, BinanceSpotFeed(callbacks), BybitSpotFeed(callbacks), MockFeed(callbacks)]  # auto

    def _market_cap_symbols(self) -> list[str]:
        """Return every source symbol needing asset-level enrichment."""
        if not self.multi_mode:
            return list(self.top_symbols)
        return list(dict.fromkeys(
            symbol
            for symbols in self.source_symbols.values()
            for symbol in symbols
        ))

    async def _select_feed(self) -> FeedConnector:
        candidates = self._candidate_feeds()
        # An explicit venue remains first choice, but a stale dashboard
        # variable must not strand production on a source whose WS edge is
        # blocked. Probe other live adapters before falling back to mock.
        if self.settings.data_feed not in ("auto", "mock", "multi"):
            selected = {feed.name for feed in candidates}
            candidates.extend(feed for feed in self._auto_candidates() if feed.name not in selected)
        for feed in candidates:
            if isinstance(feed, MockFeed):
                log.warning("no live feed reachable — falling back to MOCK feed (offline demo mode)")
                return feed
            try:
                tickers = await asyncio.wait_for(feed.fetch_universe(3), timeout=FEED_PROBE_TIMEOUT_S)
                if tickers:
                    if self.settings.data_feed != "mock":
                        await asyncio.wait_for(feed.probe_websocket([t["symbol"] for t in tickers]), timeout=8)
                    log.info("selected feed: %s (REST/WS probe ok, %d tickers)", feed.name, len(tickers))
                    return feed
            except Exception as exc:  # noqa: BLE001
                log.warning("feed %s probe failed: %s", feed.name, exc)
        return MockFeed(self._make_callbacks())

    def _auto_candidates(self) -> list[FeedConnector]:
        """Live adapters used as recovery candidates for explicit feed modes."""
        callbacks = self._make_callbacks()
        return [
            BinanceFuturesFeed(callbacks), OkxSwapFeed(callbacks),
            BybitLinearFeed(callbacks), BinanceSpotFeed(callbacks), BybitSpotFeed(callbacks),
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self.settings.data_feed == "multi":
            await self._start_multi()
            return
        self.feed = await self._select_feed()
        self.state.feed_mode = self.feed.name
        tickers = []
        with contextlib.suppress(Exception):
            tickers = await asyncio.wait_for(self.feed.fetch_universe(self.settings.top_symbols), timeout=FEED_PROBE_TIMEOUT_S)
        if tickers:
            self._seed_universe(tickers)
        else:
            self.top_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.feed.set_universe(self.top_symbols)
        self._tasks = [
            asyncio.create_task(self.feed.run(), name="feed-run"),
            asyncio.create_task(self._metrics_loop(), name="metrics-loop"),
            asyncio.create_task(self._scoring_loop(), name="scoring-loop"),
            asyncio.create_task(self._universe_loop(), name="universe-loop"),
            asyncio.create_task(self._degradation_watch(), name="degradation-watch"),
            asyncio.create_task(self._rest_fallback_loop(), name="rest-fallback"),
            asyncio.create_task(self._candle_backfill_task(), name="candle-backfill"),
            asyncio.create_task(self._levels_loop(), name="levels-loop"),
            asyncio.create_task(self._live_recovery_loop(), name="live-recovery"),
            asyncio.create_task(self.market_caps.run(self._market_cap_symbols), name="market-cap-refresh"),
        ]

    async def _start_multi(self) -> None:
        """Run isolated source feeds and merge only at snapshot serialization."""
        specs = [("binance", BinanceFuturesFeed), ("bybit", BybitLinearFeed),
                 ("binance_spot", BinanceSpotFeed), ("bybit_spot", BybitSpotFeed),
                 ("okx", OkxSwapFeed)]

        async def probe(name, cls):
            temporary = MarketState()
            feed = cls(self._make_callbacks(temporary))
            try:
                tickers = await asyncio.wait_for(feed.fetch_universe(self.settings.top_symbols), timeout=FEED_PROBE_TIMEOUT_S)
                return name, feed, temporary, tickers or []
            except Exception as exc:  # noqa: BLE001
                log.warning("multi source %s unavailable: %s", name, exc)
                return name, feed, temporary, []

        results = await asyncio.gather(*(probe(name, cls) for name, cls in specs))
        live = [result for result in results if result[3]]
        if not live:
            raise RuntimeError("DATA_FEED=multi: no source returned a universe")
        primary_name, primary_feed, primary_state, primary_tickers = live[0]
        self.multi_mode = True
        self.state.symbols = primary_state.symbols
        self.state.feed_mode = primary_name
        self.feed = primary_feed
        self.feed.callbacks = self._make_callbacks(self.state)
        self._seed_universe(primary_tickers)
        self.source_states = {primary_name: self.state}
        self.source_symbols = {primary_name: list(self.top_symbols)}
        self.extra_feeds = []
        for name, feed, source_state, tickers in live[1:]:
            source_state.feed_mode = name
            symbols = []
            for ticker in tickers:
                symbol = ticker.get("symbol")
                if not symbol:
                    continue
                source_state.get(symbol).on_ticker_24h(
                    float(ticker.get("last_price") or 0),
                    float(ticker.get("price_change_pct_24h") or 0),
                    float(ticker.get("quote_volume_24h") or 0),
                )
                symbols.append(symbol)
            feed.callbacks = self._make_callbacks(source_state)
            feed.set_universe(symbols)
            self.source_states[name] = source_state
            self.source_symbols[name] = symbols
            self.extra_feeds.append(feed)
        self.feed.set_universe(self.top_symbols)
        all_feeds = [self.feed, *self.extra_feeds]
        self._tasks = [asyncio.create_task(feed.run(), name=f"{feed.name}-run") for feed in all_feeds]
        self._tasks += [
            asyncio.create_task(self._multi_metrics_loop(), name="multi-metrics-loop"),
            asyncio.create_task(self._scoring_loop(), name="multi-scoring-loop"),
            asyncio.create_task(self._multi_backfill_task(), name="multi-backfill"),
            asyncio.create_task(self._levels_loop(), name="multi-levels-loop"),
            asyncio.create_task(self.market_caps.run(self._market_cap_symbols), name="market-cap-refresh"),
        ]
        log.info("multi source runtime started: %s", ", ".join(self.source_states))

    async def _multi_metrics_loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            now_ms = time.time() * 1000
            for name, source_state in self.source_states.items():
                for symbol in self.source_symbols.get(name, []):
                    state = source_state.symbols.get(symbol)
                    if state is None:
                        continue
                    analytics.refresh_walls(state, self.settings.wall_multiplier)
                    analytics.compute_metrics(source_state, state, now_ms)
            self.publish(self.build_snapshot())
            await asyncio.sleep(max(0.05, METRICS_TICK_S - (time.perf_counter() - started)))

    async def _multi_backfill_task(self) -> None:
        async def backfill(name, feed):
            source_state = self.source_states[name]
            for symbol in self.source_symbols.get(name, []):
                try:
                    rows = await asyncio.wait_for(feed.fetch_klines(symbol, limit=300), timeout=12)
                    state = source_state.get(symbol)
                    for t, o, h, l, c, v in rows:
                        state.on_candle(t, o, h, l, c, v)
                except Exception:
                    continue
        feeds = [(self.feed.name, self.feed), *[(feed.name, feed) for feed in self.extra_feeds]]
        await asyncio.gather(*(backfill(name, feed) for name, feed in feeds))

    async def _candle_backfill_task(self) -> None:
        await self._seed_candles()
        # re-seed when the universe grows (new symbols have no history)
        while not self._stop.is_set():
            await asyncio.sleep(60.0)
            missing = [s for s in self.top_symbols if s not in self._seeded_symbols]
            if missing:
                await self._seed_candles()

    async def _rest_fallback_loop(self) -> None:
        """Keep real ticker values moving when a venue WS is unreachable.

        Some hosted networks allow exchange REST but intermittently block or
        reset public websocket connections. Ticker snapshots are useful for a
        degraded screener, while L2/trade-derived fields remain explicitly
        stale and the health state stays degraded.
        """
        last_book_events = self.book_events
        last_trade_events = self.trade_events
        while not self._stop.is_set():
            await asyncio.sleep(REST_FALLBACK_INTERVAL_S)
            feed, generation = self.feed, self._generation
            if feed is None or isinstance(feed, MockFeed):
                continue
            if self.book_events != last_book_events or self.trade_events != last_trade_events:
                last_book_events, last_trade_events = self.book_events, self.trade_events
                continue
            try:
                tickers = await asyncio.wait_for(
                    feed.fetch_universe(max(len(self.top_symbols), self.settings.top_symbols)),
                    timeout=FEED_PROBE_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001 — fallback must never stop the streamer
                log.info("REST ticker fallback failed for %s: %s", feed.name, exc)
                continue
            if generation != self._generation or feed is not self.feed:
                continue
            tracked = set(self.top_symbols)
            for ticker in tickers:
                symbol = ticker.get("symbol")
                if symbol not in tracked:
                    continue
                st = self.state.get(symbol)
                st.on_ticker_24h(
                    float(ticker.get("last_price") or 0.0),
                    float(ticker.get("price_change_pct_24h") or 0.0),
                    float(ticker.get("quote_volume_24h") or 0.0),
                )
                self.state.record_ingest()
            if tickers:
                self.feed_degraded = True
                self.publish(self.build_snapshot())

    # ------------------------------------------------------------------
    # Live recovery (spec: upstream recovery). If boot fell back to the
    # mock feed because exchanges were unreachable (e.g. Docker network
    # still settling), re-probe every 2 minutes and hot-swap to live.
    # ------------------------------------------------------------------
    RECOVERY_PROBE_S = 120.0

    async def _live_recovery_loop(self) -> None:
        # Only recover from an *accidental* mock fallback (auto mode).
        # Explicit DATA_FEED=mock (tests / offline demo) must stay deterministic.
        if not isinstance(self.feed, MockFeed) or self.settings.data_feed != "auto":
            return
        log.warning("started in mock mode — probing for live feed recovery every %ds", self.RECOVERY_PROBE_S)
        while not self._stop.is_set():
            await asyncio.sleep(self.RECOVERY_PROBE_S)
            if self._stop.is_set() or not isinstance(self.feed, MockFeed):
                return
            for make in (BinanceFuturesFeed, BybitLinearFeed, OkxSwapFeed):
                candidate = make(self._make_callbacks())
                try:
                    probe = await asyncio.wait_for(candidate.fetch_universe(3), timeout=12)
                except Exception as exc:  # noqa: BLE001
                    log.info("recovery probe %s failed: %s", candidate.name, exc)
                    continue
                if not probe:
                    continue
                try:
                    tickers = await asyncio.wait_for(
                        candidate.fetch_universe(self.settings.top_symbols), timeout=12,
                    )
                except Exception:
                    log.warning("recovery universe fetch failed for %s", candidate.name, exc_info=True)
                    continue
                if not tickers:
                    continue
                self._activate_feed(candidate, tickers)
                self._tasks.append(asyncio.create_task(candidate.run(), name=f"{candidate.name}-run"))
                self._tasks.append(asyncio.create_task(self._seed_candles(), name="recovery-backfill"))
                self._tasks.append(asyncio.create_task(self._degradation_watch(), name="degradation-watch-recovered"))
                return
        log.info("streamer started: feed=%s symbols=%d", self.feed.name, len(self.top_symbols))

    # ------------------------------------------------------------------
    # Candle backfill: seed 1m history from the exchange REST so charts
    # show ~5 hours of candles immediately after a cold boot.
    # ------------------------------------------------------------------
    def _activate_feed(self, candidate: FeedConnector, tickers: list[dict[str, Any]]) -> None:
        """Commit a fully probed source without carrying market history across venues.

        No awaits inside this boundary: late callbacks/REST results from the old
        generation are rejected before they can mutate the new source's state.
        Keep shared state/scorer objects because the API and alerts reference them.
        """
        if not tickers:
            raise ValueError("cannot activate an empty universe")
        self._generation += 1
        if self.feed:
            self.feed.stop()
        for feed in self.extra_feeds:
            feed.stop()
        self.extra_feeds.clear()
        self.state.symbols.clear()
        self.state.last_message_ts_ms = None
        self.scorer.reset()
        self.alerts.clear()
        self._fired_alerts.clear()
        self._seeded_symbols.clear()
        self.book_events = self.trade_events = 0
        self.feed_degraded = self.hybrid_mode = False
        candidate.callbacks = self._make_callbacks()
        self.feed = candidate
        self.state.feed_mode = candidate.name
        self._seed_universe(tickers)
        candidate.set_universe(self.top_symbols)
        self.publish(self.build_snapshot())

    async def _seed_candles(self) -> None:
        async with self._backfill_lock:
            await self._seed_candles_locked()

    async def _seed_candles_locked(self) -> None:
        feed, generation = self.feed, self._generation
        if not hasattr(feed, "fetch_klines"):
            return
        to_seed = [s for s in self.top_symbols if s not in self._seeded_symbols]
        for sym in to_seed:
            try:
                klines = await asyncio.wait_for(feed.fetch_klines(sym, limit=300), timeout=20)
                if generation != self._generation:
                    return
                st = self.state.get(sym)
                for t, o, h, l, c, v in klines:
                    st.on_candle(t, o, h, l, c, v)
                self._seeded_symbols.add(sym)
                await asyncio.sleep(0.15)  # stay well under REST rate limits
            except Exception:  # noqa: BLE001 — a failed backfill must not block boot
                log.warning("candle backfill failed for %s", sym, exc_info=True)
        if to_seed:
            log.info("candle backfill complete: %d/%d symbols", len(self._seeded_symbols), len(self.top_symbols))

    # ------------------------------------------------------------------
    # Pivot/cascade detection over the 1m candle history (spec section 5).
    # Cheap enough to recompute every 30s per symbol.
    # ------------------------------------------------------------------
    LEVELS_INTERVAL_S = 30.0

    async def _levels_loop(self) -> None:
        from app.levels import build_cascades, detect_pivots

        while not self._stop.is_set():
            started = time.perf_counter()
            sources = (
                [(name, state, self.source_symbols.get(name, []))
                 for name, state in self.source_states.items()]
                if self.multi_mode else [(self.state.feed_mode, self.state, self.top_symbols)]
            )
            now_ms = time.time() * 1000
            for name, source_state, symbols in sources:
                for sym in symbols:
                    st = source_state.symbols.get(sym)
                    if st is None or len(st.candles) < 10:
                        continue
                    try:
                        closed = [c for c in st.candles if c.open_time + 60_000 <= now_ms]
                        pivots = detect_pivots(closed, k=3)
                        st.pivots = pivots
                        st.cascades = build_cascades(closed, pivots, min_touches=3)
                    except Exception:  # noqa: BLE001 — isolate analytics failures by source
                        log.exception("pivot detection failed for %s:%s", name, sym)
            self.publish(self.build_snapshot())
            await asyncio.sleep(max(1.0, self.LEVELS_INTERVAL_S - (time.perf_counter() - started)))

    async def stop(self) -> None:
        self._stop.set()
        await self.history.close()
        await self.market_caps.close()
        if self.feed:
            self.feed.stop()
        for f in self.extra_feeds:
            f.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _seed_universe(self, tickers: list[dict[str, Any]]) -> None:
        for t in tickers:
            st = self.state.get(t["symbol"])
            st.on_ticker_24h(t["last_price"], t["price_change_pct_24h"], t["quote_volume_24h"])
        self.top_symbols = [t["symbol"] for t in tickers]

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------
    async def _universe_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(UNIVERSE_REFRESH_S)
            if not self.feed or isinstance(self.feed, MockFeed):
                continue
            try:
                feed, generation = self.feed, self._generation
                tickers = await feed.fetch_universe(self.settings.top_symbols)
                if generation != self._generation:
                    continue
                self._seed_universe(tickers)
                self.feed.set_universe(self.top_symbols)
                for f in self.extra_feeds:
                    f.set_universe(self.top_symbols)
                # backfill candles for any newly added symbols
                await self._seed_candles()
            except Exception:  # noqa: BLE001
                log.exception("universe refresh failed")

    # ------------------------------------------------------------------
    # A partial source must never be repaired by injecting another venue's
    # trades/candles into the same symbol state. Report degradation explicitly.
    # The connector's own reconnect loop handles transport recovery.
    # ------------------------------------------------------------------
    DEGRADE_WARMUP_S = 25.0
    FAILOVER_AFTER_SILENT_ROUNDS = 2

    async def _attempt_failover(self, generation: int) -> None:
        """Move to another live connector when a probed source has no WS data.

        REST universe discovery alone is not enough to call a source healthy: a
        venue can answer REST while its websocket is blocked or unavailable.
        In auto mode, probe the remaining connectors and activate the first one
        that has both a universe and a fresh generation boundary.
        """
        if self.settings.data_feed != "auto" or generation != self._generation:
            return
        async with self._failover_lock:
            if generation != self._generation or self._stop.is_set():
                return
            current = self.feed.name if self.feed else ""
            candidates = [
                BinanceFuturesFeed(self._make_callbacks()),
                OkxSwapFeed(self._make_callbacks()),
                BybitLinearFeed(self._make_callbacks()),
                BinanceSpotFeed(self._make_callbacks()),
                BybitSpotFeed(self._make_callbacks()),
            ]
            for candidate in candidates:
                if candidate.name == current:
                    continue
                try:
                    probe = await asyncio.wait_for(candidate.fetch_universe(3), timeout=FEED_PROBE_TIMEOUT_S)
                    if not probe:
                        continue
                    tickers = await asyncio.wait_for(
                        candidate.fetch_universe(self.settings.top_symbols), timeout=FEED_PROBE_TIMEOUT_S,
                    )
                    if not tickers:
                        continue
                    self._activate_feed(candidate, tickers)
                    self._tasks.append(asyncio.create_task(candidate.run(), name=f"{candidate.name}-run"))
                    self._tasks.append(asyncio.create_task(self._seed_candles(), name="failover-backfill"))
                    self._tasks.append(asyncio.create_task(self._degradation_watch(), name="degradation-watch-failover"))
                    log.warning("failed over from silent source %s to %s", current, candidate.name)
                    return
                except Exception as exc:  # noqa: BLE001 — try the next venue
                    log.info("failover probe %s failed: %s", candidate.name, exc)
            log.warning("no alternate live source available after silent %s feed", current)

    async def _degradation_watch(self) -> None:
        generation = self._generation
        previous_books, previous_trades = self.book_events, self.trade_events
        silent_rounds = 0
        while not self._stop.is_set() and generation == self._generation:
            await asyncio.sleep(self.DEGRADE_WARMUP_S)
            if generation != self._generation or self._stop.is_set():
                return
            if self.feed is None or isinstance(self.feed, MockFeed):
                return
            books = self.book_events - previous_books
            trades = self.trade_events - previous_trades
            self.feed_degraded = (books == 0 and trades == 0) or (books > 50 and trades == 0)
            previous_books, previous_trades = self.book_events, self.trade_events
            if self.feed_degraded:
                silent_rounds += 1
                log.warning("source %s is silent or has books without trades; keeping sources isolated", self.feed.name)
                if silent_rounds >= self.FAILOVER_AFTER_SILENT_ROUNDS:
                    await self._attempt_failover(generation)
                    # Keep watching when all alternate probes are currently
                    # unavailable; the next interval gets another chance.
                    if generation != self._generation:
                        return
                    silent_rounds = 1
            else:
                silent_rounds = 0
            self.publish(self.build_snapshot())

    async def _metrics_loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            now_ms = time.time() * 1000
            for sym in self.top_symbols:
                st = self.state.symbols.get(sym)
                if st is None:
                    continue
                analytics.refresh_walls(st, self.settings.wall_multiplier)
                analytics.compute_metrics(self.state, st, now_ms)
            self.publish(self.build_snapshot())
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.05, METRICS_TICK_S - elapsed))

    async def _scoring_loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            now_ms = time.time() * 1000
            for sym in self.top_symbols:
                with contextlib.suppress(Exception):
                    self.scorer.score_symbol(self.state, sym, now_ms)
            self.last_scoring_at = time.time()
            self._check_alerts(now_ms)
            self.publish(self.build_snapshot())
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(1.0, self.settings.ml_score_interval - elapsed))

    # ------------------------------------------------------------------
    # Alerts (score >= 85, per-symbol cooldown 5 min)
    # ------------------------------------------------------------------
    def _check_alerts(self, now_ms: float) -> None:
        for pick in self.scorer.picks.values():
            if pick.scalp_score < 85.0:
                continue
            last = self._fired_alerts.get(pick.symbol, 0.0)
            if now_ms - last < 5 * 60_000:
                continue
            self._fired_alerts[pick.symbol] = now_ms
            self.alerts.append(
                {
                    "symbol": pick.symbol,
                    "score": pick.scalp_score,
                    "tag": pick.tag,
                    "thesis": pick.thesis,
                    "ts": now_ms,
                }
            )
            log.info("ALERT %s score=%.0f tag=%s", pick.symbol, pick.scalp_score, pick.tag)
        self.alerts = self.alerts[-50:]

    def _serialize_source_row(self, source_name: str, source_state: MarketState, symbol: str) -> dict[str, Any] | None:
        state = source_state.symbols.get(symbol)
        if state is None:
            return None
        venue = venue_for_feed(source_name)
        market = market_for_feed(source_name)
        cap = self.market_caps.records.get(self.market_caps._key(symbol))
        if cap:
            state.asset_id = cap.asset_id
            state.market_cap_usd = cap.market_cap_usd
            state.market_cap_source = cap.source
            state.market_cap_updated_at = cap.updated_at
        metrics = state.metrics
        walls = sorted(state.walls.values(), key=lambda wall: wall["notional_usd"], reverse=True)[:5]
        return {
            "symbol": symbol, "instrument_id": f"{venue}:{market}:{symbol}",
            "venue": venue, "market": market, "price": metrics.price,
            "change5m": metrics.price_change_5m_pct, "change24h": metrics.price_change_24h_pct,
            "vol1m": metrics.volume_1m_usd, "vol24h": state.quote_volume_24h,
            "assetId": state.asset_id, "marketCapUsd": state.market_cap_usd,
            "marketCapSource": state.market_cap_source, "marketCapUpdatedAt": state.market_cap_updated_at,
            "vol5m": metrics.volume_5m_usd, "surge": metrics.volume_surge_ratio,
            "imbalance": metrics.book_imbalance, "spreadBps": metrics.spread_bps,
            "depth": metrics.depth_notional_usd, "tps": metrics.trade_velocity_tps,
            "takerBuy": metrics.taker_buy_ratio_1m, "bbPctile": metrics.bb_width_pctile,
            "funding": metrics.funding_rate, "nearestWall": metrics.nearest_wall_distance_pct,
            "natr": metrics.natr_pct, "speed": metrics.speed_pct_per_min,
            "range5m": metrics.range_5m_pct, "surgeClosed": metrics.surge_closed,
            "levelDist": metrics.level_dist_pct, "levelKind": metrics.level_kind,
            "score": None, "tag": None, "thesis": None,
            "walls": [{"side": wall["side"], "price": wall["price"], "notional": wall["notional_usd"],
                       "distance": wall["distance_pct"], "eat": wall.get("seconds_to_eat"),
                       "category": wall.get("category"), "brp": wall.get("brp_usd")} for wall in walls],
            "levels": [{"kind": zone.kind, "low": zone.low, "high": zone.high, "mid": zone.mid,
                        "touches": zone.touches, "timeframe": "1m"}
                       for zone in sorted(state.cascades, key=lambda zone: zone.touches, reverse=True)[:10]],
            "bookTs": state.book_ts_ms,
            "densityCoverage": {"kind": "top_n_levels", "levelsBid": state.density_bid_levels,
                                "levelsAsk": state.density_ask_levels, "completePercentRange": False},
        }

    # ------------------------------------------------------------------
    # Snapshot serialization
    # ------------------------------------------------------------------
    def build_snapshot(self) -> dict[str, Any]:
        now_ms = time.time() * 1000
        feed_name = self.feed.name if self.feed else ""
        venue = venue_for_feed(feed_name)
        market = market_for_feed(feed_name)
        symbols = []
        for sym in self.top_symbols:
            st = self.state.symbols.get(sym)
            if st is None:
                continue
            cap = self.market_caps.records.get(self.market_caps._key(sym))
            if cap:
                st.asset_id = cap.asset_id
                st.market_cap_usd = cap.market_cap_usd
                st.market_cap_source = cap.source
                st.market_cap_updated_at = cap.updated_at
            m = st.metrics
            pick = self.scorer.picks.get(sym)
            walls = sorted(st.walls.values(), key=lambda w: w["notional_usd"], reverse=True)[:5]
            symbols.append(
                {
                    "symbol": sym,
                    "instrument_id": f"{venue}:{market}:{sym}",
                    "venue": venue,
                    "market": market,
                    "price": m.price,
                    "change5m": m.price_change_5m_pct,
                    "change24h": m.price_change_24h_pct,
                    "vol1m": m.volume_1m_usd,
                    "vol24h": st.quote_volume_24h,
                    "assetId": st.asset_id,
                    "marketCapUsd": st.market_cap_usd,
                    "marketCapSource": st.market_cap_source,
                    "marketCapUpdatedAt": st.market_cap_updated_at,
                    "vol5m": m.volume_5m_usd,
                    "surge": m.volume_surge_ratio,
                    "imbalance": m.book_imbalance,
                    "spreadBps": m.spread_bps,
                    "depth": m.depth_notional_usd,
                    "tps": m.trade_velocity_tps,
                    "takerBuy": m.taker_buy_ratio_1m,
                    "bbPctile": m.bb_width_pctile,
                    "funding": m.funding_rate,
                    "nearestWall": m.nearest_wall_distance_pct,
                    "natr": m.natr_pct,
                    "speed": m.speed_pct_per_min,
                    "range5m": m.range_5m_pct,
                    "surgeClosed": m.surge_closed,
                    "levelDist": m.level_dist_pct,
                    "levelKind": m.level_kind,
                    "score": pick.scalp_score if pick else None,
                    "tag": pick.tag if pick else None,
                    "thesis": pick.thesis if pick else None,
                    "walls": [
                        {
                            "side": w["side"],
                            "price": w["price"],
                            "notional": w["notional_usd"],
                            "distance": w["distance_pct"],
                            "eat": w.get("seconds_to_eat"),
                            "category": w.get("category"),
                            "brp": w.get("brp_usd"),
                        }
                        for w in walls
                    ],
                    "levels": [
                        {"kind": z.kind, "low": z.low, "high": z.high,
                         "mid": z.mid, "touches": z.touches, "timeframe": "1m"}
                        for z in sorted(st.cascades, key=lambda z: z.touches, reverse=True)[:10]
                    ],
                    "bookTs": st.book_ts_ms,
                    "densityCoverage": {
                        "kind": "top_n_levels",
                        "levelsBid": st.density_bid_levels,
                        "levelsAsk": st.density_ask_levels,
                        "completePercentRange": False,
                    },
                }
            )
        if self.multi_mode:
            for source_name, source_state in self.source_states.items():
                if source_state is self.state:
                    continue
                for symbol in self.source_symbols.get(source_name, []):
                    row = self._serialize_source_row(source_name, source_state, symbol)
                    if row is not None:
                        symbols.append(row)

        picks = [
            {
                "symbol": p.symbol,
                "instrument_id": f"{venue}:{market}:{p.symbol}",
                "score": p.scalp_score,
                "heuristic": p.heuristic_score,
                "anomaly": p.anomaly_score,
                "tag": p.tag,
                "thesis": p.thesis,
                "price": p.metrics.price,
                "surge": p.metrics.volume_surge_ratio,
                "imbalance": p.metrics.book_imbalance,
            }
            for p in self.scorer.top(5)
        ]
        snapshot = {
            "type": "snapshot",
            "ts": now_ms,
            "feed": "multi" if self.multi_mode else self.state.feed_mode,
            "feedDegraded": self.feed_degraded,
            "sourceGeneration": self._generation,
            "symbols": symbols,
            "picks": picks,
            "alerts": self.alerts[-10:],
            "stats": {
                "tracked": sum(len(values) for values in self.source_symbols.values()) if self.multi_mode else len(self.top_symbols),
                "ingested": self.state.messages_ingested,
                "latencyMs": round(self.state.ingest_latency_ema_ms, 1),
            },
        }
        self._last_snapshot = snapshot
        # Encode once on the metrics loop; HTTP requests only copy these bytes.
        overview_fields = {
            "symbol", "instrument_id", "venue", "market", "price", "change5m",
            "change24h", "vol1m", "vol24h", "marketCapUsd", "vol5m", "surge",
            "imbalance", "spreadBps", "depth", "tps", "funding", "nearestWall",
            "natr", "speed", "range5m", "levelDist", "levelKind", "score", "tag",
            "bookTs", "densityCoverage",
            "walls", "levels",
        }
        compact_symbols = [{key: row.get(key) for key in overview_fields} for row in symbols]
        self._last_overview_body = json.dumps(
            {"ts": int(now_ms), "symbols": compact_symbols}, separators=(",", ":")
        ).encode("utf-8")
        return snapshot
