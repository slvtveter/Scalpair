"""In-memory market state store.

Single-writer (the asyncio event loop) shared structure holding the latest
order book snapshot, rolling trade aggregation buckets, candles, walls and
derived metrics for every tracked symbol. Designed for O(1) ingestion per
message with heavier analytics computed on a separate tick cadence.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from app.models import SymbolMetrics

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
BUCKETS_KEEP_MINUTES = 35          # per-minute aggregation history window
PRICE_HISTORY_KEEP_S = 660         # 1-second sampled price history window
LEVEL_SAMPLE_INTERVAL_S = 2.0      # order book level-notional sampling cadence
LEVEL_SAMPLES_KEEP = 5 * 60 // int(LEVEL_SAMPLE_INTERVAL_S) * 48  # ~5min @ 48 levels


def minute_bucket(ts_ms: float) -> int:
    """Floor a millisecond timestamp to a minute bucket key (unix seconds)."""
    return int(ts_ms // 60_000)


@dataclass
class TradeBucket:
    """Aggregated trades for one minute."""

    notional: float = 0.0
    buy_notional: float = 0.0    # aggressive buys (taker = buyer)
    sell_notional: float = 0.0   # aggressive sells
    count: int = 0


@dataclass
class Candle:
    open_time: int  # unix ms of bucket start
    open: float
    high: float
    low: float
    close: float
    volume: float  # base qty


@dataclass
class SymbolState:
    """Everything we know about one perpetual contract."""

    symbol: str
    # --- latest book snapshot (top levels only) ---
    bids: list[tuple[float, float]] = field(default_factory=list)  # desc by price
    asks: list[tuple[float, float]] = field(default_factory=list)  # asc by price
    book_ts_ms: float = 0.0

    # --- last trade / price ---
    price: float = 0.0
    price_change_24h_pct: float = 0.0
    quote_volume_24h: float = 0.0
    funding_rate: float = 0.0
    open_interest_usd: float = 0.0

    # --- rolling series ---
    price_history: deque = field(default_factory=lambda: deque(maxlen=PRICE_HISTORY_KEEP_S))
    buckets: dict[int, TradeBucket] = field(default_factory=dict)
    candles: deque = field(default_factory=lambda: deque(maxlen=300))  # 1m candles
    level_samples: deque = field(default_factory=lambda: deque(maxlen=LEVEL_SAMPLES_KEEP))
    _last_level_sample_ms: float = 0.0
    _cur_candle: Candle | None = None

    # --- detections ---
    walls: dict[tuple[str, float], dict] = field(default_factory=dict)
    pivots: list = field(default_factory=list)     # app.levels.Pivot
    cascades: list = field(default_factory=list)   # app.levels.Cascade

    # --- derived ---
    metrics: SymbolMetrics = field(default=None, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.metrics = SymbolMetrics(symbol=self.symbol)

    # ------------------------------------------------------------------
    # Ingestion hooks (O(1) / O(depth))
    # ------------------------------------------------------------------
    def on_book(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts_ms: float) -> None:
        self.bids = bids
        self.asks = asks
        self.book_ts_ms = ts_ms
        # Throttled sampling of per-level notionals for the rolling median.
        if ts_ms - self._last_level_sample_ms >= LEVEL_SAMPLE_INTERVAL_S * 1000:
            self._last_level_sample_ms = ts_ms
            for lvl in bids[:24] + asks[:24]:
                self.level_samples.append(lvl[0] * lvl[1])

    def on_trade(self, ts_ms: float, price: float, qty: float, is_buyer_maker: bool) -> None:
        self.price = price
        self.price_history.append((ts_ms, price))
        notional = price * qty
        key = minute_bucket(ts_ms)
        b = self.buckets.get(key)
        if b is None:
            b = self.buckets[key] = TradeBucket()
        b.notional += notional
        b.count += 1
        # Binance `m` (buyer is maker) => taker sold. Bybit `S=Sell` => taker sold.
        if is_buyer_maker:
            b.sell_notional += notional
        else:
            b.buy_notional += notional
        self._update_candle(ts_ms, price, qty)
        self._prune(ts_ms)

    def on_ticker_24h(self, last_price: float, change_pct: float, quote_volume: float) -> None:
        self.price_change_24h_pct = change_pct
        self.quote_volume_24h = quote_volume
        if last_price > 0:
            self.price = last_price

    # ------------------------------------------------------------------
    def _update_candle(self, ts_ms: float, price: float, qty: float) -> None:
        key = int(ts_ms // 60_000) * 60_000
        c = self._cur_candle
        if c is None or c.open_time != key:
            c = self._cur_candle = Candle(key, price, price, price, price, qty)
            self.candles.append(c)
        else:
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.volume += qty

    def on_candle(self, open_time_ms: int, o: float, h: float, l: float, c: float, v: float) -> None:
        """Merge a kline/candle update (deduped by bucket key with trade-built candles)."""
        key = int(open_time_ms // 60_000) * 60_000
        cur = self._cur_candle
        if cur is not None and cur.open_time == key:
            cur.open = o
            cur.high = max(cur.high, h)
            cur.low = min(cur.low, l)
            cur.close = c
            cur.volume = max(cur.volume, v)
            return
        c2 = Candle(key, o, h, l, c, v)
        self._cur_candle = c2
        self.candles.append(c2)

    def _prune(self, now_ms: float) -> None:
        cutoff_bucket = minute_bucket(now_ms) - 60
        if len(self.buckets) > BUCKETS_KEEP_MINUTES + 2:
            for k in [k for k in self.buckets if k < cutoff_bucket]:
                del self.buckets[k]
        if self.price_history and now_ms / 1000 - self.price_history[0][0] / 1000 > PRICE_HISTORY_KEEP_S:
            # deque is 1s-sampled; trim stale head lazily
            while self.price_history and now_ms - self.price_history[0][0] > PRICE_HISTORY_KEEP_S * 1000:
                self.price_history.popleft()

    # ------------------------------------------------------------------
    # Window queries
    # ------------------------------------------------------------------
    def bucket_range(self, now_ms: float, minutes: int) -> list[TradeBucket]:
        """Buckets for the last `minutes` minutes, oldest first (excludes partial current)."""
        now_key = minute_bucket(now_ms)
        return [self.buckets[k] for k in range(now_key - minutes, now_key) if k in self.buckets]

    def volume_1m(self, now_ms: float) -> float:
        return self.buckets.get(minute_bucket(now_ms) - 1, TradeBucket()).notional

    def volume_5m(self, now_ms: float) -> float:
        return sum(b.notional for b in self.bucket_range(now_ms, 5))

    def volume_surge_ratio(self, now_ms: float) -> float:
        """V(1m) / mean(V over the last 30x1m buckets)."""
        base = self.bucket_range(now_ms, 31)
        if not base:
            return 0.0
        mean_1m = sum(b.notional for b in base) / len(base)
        if mean_1m <= 0:
            return 0.0
        return self.volume_1m(now_ms) / mean_1m

    def taker_buy_ratio_1m(self, now_ms: float) -> float:
        b = self.buckets.get(minute_bucket(now_ms) - 1)
        if not b or b.notional <= 0:
            return 0.5
        return b.buy_notional / b.notional

    def trade_velocity_tps(self, now_ms: float) -> float:
        b = self.buckets.get(minute_bucket(now_ms) - 1)
        return b.count / 60.0 if b else 0.0

    def price_change_5m(self, now_ms: float) -> float:
        target = now_ms - 5 * 60_000
        ref = None
        # last sample at or before the 5m-ago mark
        for ts, p in self.price_history:
            if ts <= target:
                ref = p
            else:
                break
        if ref is None:
            ref = self.price_history[0][1] if self.price_history else None
        if ref is None or self.price <= 0:
            return 0.0
        return (self.price - ref) / ref * 100.0

    def spread_bps(self) -> float:
        if not self.bids or not self.asks or self.price <= 0:
            return 0.0
        spread = self.asks[0][0] - self.bids[0][0]
        return spread / self.price * 10_000.0

    def depth_notional(self, levels: int = 20) -> float:
        return sum(p * q for p, q in self.bids[:levels]) + sum(p * q for p, q in self.asks[:levels])

    def imbalance(self, levels: int = 10) -> float:
        if not self.bids or not self.asks:
            return 0.0
        bid_sum = sum(q for _, q in self.bids[:levels])
        ask_sum = sum(q for _, q in self.asks[:levels])
        denom = bid_sum + ask_sum
        return (bid_sum - ask_sum) / denom if denom > 0 else 0.0

    def median_level_notional(self) -> float:
        if len(self.level_samples) < 20:
            return 0.0
        return float(np.median(np.fromiter(self.level_samples, dtype=float, count=len(self.level_samples))))

    def bollinger_width_percentile(self, period: int = 30) -> float:
        """Percentile of the latest BB width within the last `period` candles.

        Low percentile => volatility compression (squeeze).
        """
        if len(self.candles) < period:
            return 0.5
        closes = [c.close for c in list(self.candles)[-period:]]
        std = float(np.std(closes))
        mean = float(np.mean(closes))
        if mean <= 0:
            return 0.5
        width = 4 * std / mean * 100  # 2-sigma band width in %
        widths = []
        candles = list(self.candles)
        for i in range(period, len(candles) + 1):
            w = candles[i - period:i]
            closes_w = [c.close for c in w]
            m = float(np.mean(closes_w))
            if m > 0:
                widths.append(4 * float(np.std(closes_w)) / m * 100)
        if not widths:
            return 0.5
        return float(np.mean([w <= width for w in widths]))


class MarketState:
    """Global store + ingest counters shared by feeds, engine and API."""

    def __init__(self) -> None:
        self.symbols: dict[str, SymbolState] = {}
        self.started_at = time.time()
        self.messages_ingested = 0
        self.ingest_latency_ema_ms = 0.0
        self.last_message_ts_ms: float | None = None
        self.feed_mode = "boot"

    # ------------------------------------------------------------------
    def get(self, symbol: str) -> SymbolState:
        st = self.symbols.get(symbol)
        if st is None:
            st = self.symbols[symbol] = SymbolState(symbol)
        return st

    def tracked(self) -> list[str]:
        return list(self.symbols.keys())

    def record_ingest(self, msg_ts_ms: float | None = None) -> None:
        self.messages_ingested += 1
        now_ms = time.time() * 1000
        self.last_message_ts_ms = now_ms
        if msg_ts_ms:
            latency = max(0.0, now_ms - msg_ts_ms)
            # exponential moving average, 2% weight
            self.ingest_latency_ema_ms = (
                latency if self.ingest_latency_ema_ms == 0
                else self.ingest_latency_ema_ms * 0.98 + latency * 0.02
            )

    def message_age_ms(self) -> float | None:
        if self.last_message_ts_ms is None:
            return None
        return max(0.0, time.time() * 1000 - self.last_message_ts_ms)
