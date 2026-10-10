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
import logging
import time
from typing import Any

from app import analytics
from app.history import HistoryCache
from app.instruments import venue_for_feed
from app.config import Settings
from app.feeds.base import FeedCallbacks, FeedConnector
from app.feeds.binance import BinanceFuturesFeed
from app.feeds.bybit import BybitLinearFeed
from app.feeds.okx import OkxSwapFeed
from app.feeds.mock import MockFeed
from app.ml_engine import ScalpScorer
from app.state import MarketState

log = logging.getLogger("scalpair.streamer")

METRICS_TICK_S = 0.25  # 4 Hz snapshot publish; WS_BROADCAST_RATE caps per-client delivery
UNIVERSE_REFRESH_S = 60.0
FEED_PROBE_TIMEOUT_S = 10.0


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

    # ------------------------------------------------------------------
    # Callbacks (feed → state)
    # ------------------------------------------------------------------
    def _make_callbacks(self) -> FeedCallbacks:
        state = self.state
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
        if mode == "bybit":
            return [bybit]
        if mode == "okx":
            return [OkxSwapFeed(callbacks)]
        if mode == "mock":
            return [MockFeed(callbacks)]
        return [binance, bybit, OkxSwapFeed(callbacks), MockFeed(callbacks)]  # auto

    async def _select_feed(self) -> FeedConnector:
        for feed in self._candidate_feeds():
            if isinstance(feed, MockFeed):
                log.warning("no live feed reachable — falling back to MOCK feed (offline demo mode)")
                return feed
            try:
                tickers = await asyncio.wait_for(feed.fetch_universe(3), timeout=FEED_PROBE_TIMEOUT_S)
                if tickers:
                    log.info("selected feed: %s (probe ok, %d tickers)", feed.name, len(tickers))
                    return feed
            except Exception as exc:  # noqa: BLE001
                log.warning("feed %s probe failed: %s", feed.name, exc)
        return MockFeed(self._make_callbacks())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
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
            asyncio.create_task(self._candle_backfill_task(), name="candle-backfill"),
            asyncio.create_task(self._levels_loop(), name="levels-loop"),
            asyncio.create_task(self._live_recovery_loop(), name="live-recovery"),
        ]

    async def _candle_backfill_task(self) -> None:
        await self._seed_candles()
        # re-seed when the universe grows (new symbols have no history)
        while not self._stop.is_set():
            await asyncio.sleep(60.0)
            missing = [s for s in self.top_symbols if s not in self._seeded_symbols]
            if missing:
                await self._seed_candles()

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
            for sym in self.top_symbols:
                st = self.state.symbols.get(sym)
                if st is None or len(st.candles) < 10:
                    continue
                try:
                    closed = [c for c in st.candles if c.open_time + 60_000 <= time.time() * 1000]
                    pivots = detect_pivots(closed, k=3)
                    st.pivots = pivots
                    st.cascades = build_cascades(closed, pivots, min_touches=3)
                except Exception:  # noqa: BLE001 — analytics must not kill the loop
                    log.exception("pivot detection failed for %s", sym)
            self.publish(self.build_snapshot())
            await asyncio.sleep(max(1.0, self.LEVELS_INTERVAL_S - (time.perf_counter() - started)))

    async def stop(self) -> None:
        self._stop.set()
        await self.history.close()
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

    async def _degradation_watch(self) -> None:
        generation = self._generation
        previous_books, previous_trades = self.book_events, self.trade_events
        while not self._stop.is_set() and generation == self._generation:
            await asyncio.sleep(self.DEGRADE_WARMUP_S)
            if generation != self._generation or self._stop.is_set():
                return
            if self.feed is None or isinstance(self.feed, MockFeed):
                return
            books = self.book_events - previous_books
            trades = self.trade_events - previous_trades
            self.feed_degraded = books > 50 and trades == 0
            previous_books, previous_trades = self.book_events, self.trade_events
            if self.feed_degraded:
                log.warning("source %s has books but no trades; keeping sources isolated", self.feed.name)
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

    # ------------------------------------------------------------------
    # Snapshot serialization
    # ------------------------------------------------------------------
    def build_snapshot(self) -> dict[str, Any]:
        now_ms = time.time() * 1000
        feed_name = self.feed.name if self.feed else ""
        venue = venue_for_feed(feed_name)
        symbols = []
        for sym in self.top_symbols:
            st = self.state.symbols.get(sym)
            if st is None:
                continue
            m = st.metrics
            pick = self.scorer.picks.get(sym)
            walls = sorted(st.walls.values(), key=lambda w: w["notional_usd"], reverse=True)[:5]
            symbols.append(
                {
                    "symbol": sym,
                    "instrument_id": f"{venue}:FUTURES:{sym}",
                    "venue": venue,
                    "market": "FUTURES",
                    "price": m.price,
                    "change5m": m.price_change_5m_pct,
                    "change24h": m.price_change_24h_pct,
                    "vol1m": m.volume_1m_usd,
                    "vol24h": st.quote_volume_24h,
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
                        }
                        for w in walls
                    ],
                    "levels": [
                        {"kind": z.kind, "low": z.low, "high": z.high,
                         "mid": z.mid, "touches": z.touches, "timeframe": "1m"}
                        for z in sorted(st.cascades, key=lambda z: z.touches, reverse=True)[:10]
                    ],
                    "bookTs": st.book_ts_ms,
                }
            )
        picks = [
            {
                "symbol": p.symbol,
                "instrument_id": f"{venue}:FUTURES:{p.symbol}",
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
        return {
            "type": "snapshot",
            "ts": now_ms,
            "feed": self.state.feed_mode,
            "feedDegraded": self.feed_degraded,
            "sourceGeneration": self._generation,
            "symbols": symbols,
            "picks": picks,
            "alerts": self.alerts[-10:],
            "stats": {
                "tracked": len(self.top_symbols),
                "ingested": self.state.messages_ingested,
                "latencyMs": round(self.state.ingest_latency_ema_ms, 1),
            },
        }
