"""Unit tests for analytics: imbalance, wall detection, flow z-score, rolling windows."""

from __future__ import annotations

import numpy as np
import pytest

from app.analytics import (
    aggressive_flow_zscore,
    detect_walls,
    imbalance_ratio,
    refresh_walls,
    seconds_to_eat_wall,
)
from app.state import SymbolState, TradeBucket, minute_bucket


# ---------------------------------------------------------------------------
# imbalance_ratio
# ---------------------------------------------------------------------------
class TestImbalanceRatio:
    def test_symmetric_book_is_zero(self):
        bids = [(100.0, 5.0), (99.0, 5.0)]
        asks = [(101.0, 5.0), (102.0, 5.0)]
        assert imbalance_ratio(bids, asks) == pytest.approx(0.0)

    def test_all_bids_is_one(self):
        bids = [(100.0, 10.0)]
        asks = [(101.0, 0.0)]
        assert imbalance_ratio(bids, asks) == pytest.approx(1.0)

    def test_all_asks_is_minus_one(self):
        bids = [(100.0, 0.0)]
        asks = [(101.0, 10.0)]
        assert imbalance_ratio(bids, asks) == pytest.approx(-1.0)

    def test_empty_book_is_zero(self):
        assert imbalance_ratio([], []) == 0.0

    def test_levels_argument_limits_window(self):
        bids = [(100.0, 5.0), (99.0, 50.0)]
        asks = [(101.0, 5.0), (102.0, 5.0)]
        # top-1 only: 5 vs 5 -> zero
        assert imbalance_ratio(bids, asks, levels=1) == pytest.approx(0.0)
        # top-2: (55-10)/65
        assert imbalance_ratio(bids, asks, levels=2) == pytest.approx(45.0 / 65.0)


# ---------------------------------------------------------------------------
# detect_walls
# ---------------------------------------------------------------------------
class TestDetectWalls:
    def test_wall_above_multiplier_detected(self):
        bids = [(100.0, 10.0)] + [(99.0 - i, 1.0) for i in range(10)]
        asks = [(101.0, 1.0)] + [(102.0 + i, 1.0) for i in range(10)]
        walls = detect_walls(bids, asks, median_level_notional=50.0, multiplier=3.0)
        assert len(walls) == 1
        assert walls[0]["side"] == "bid"
        assert walls[0]["price"] == 100.0
        assert walls[0]["notional_usd"] == pytest.approx(1000.0)

    def test_no_walls_below_threshold(self):
        bids = [(100.0, 1.0), (99.0, 1.0)]
        asks = [(101.0, 1.0), (102.0, 1.0)]
        assert detect_walls(bids, asks, 50.0, 3.0) == []

    def test_zero_median_returns_empty(self):
        assert detect_walls([(100.0, 999.0)], [(101.0, 999.0)], 0.0, 3.0) == []

    def test_distance_signed(self):
        bids = [(100.0, 1000.0)]
        asks = [(104.0, 1000.0)]
        walls = detect_walls(bids, asks, 100.0, 3.0)
        d = {w["side"]: w["distance_pct"] for w in walls}
        assert d["bid"] < 0 < d["ask"]

    def test_sorted_by_notional(self):
        bids = [(100.0, 1.0), (99.0, 50.0), (98.0, 10.0)]
        asks = [(101.0, 1.0), (102.0, 1.0)]
        walls = detect_walls(bids, asks, 1.0, 3.0)
        notionals = [w["notional_usd"] for w in walls]
        assert notionals == sorted(notionals, reverse=True)


# ---------------------------------------------------------------------------
# seconds_to_eat_wall
# ---------------------------------------------------------------------------
class TestSecondsToEat:
    def test_basic(self):
        # $600k wall, $2M traded in 5m => 6_666 usd/s => 90s
        assert seconds_to_eat_wall(600_000, 2_000_000) == pytest.approx(90.0, rel=0.01)

    def test_zero_volume_returns_none(self):
        assert seconds_to_eat_wall(600_000, 0) is None


# ---------------------------------------------------------------------------
# flow z-score + rolling windows on SymbolState
# ---------------------------------------------------------------------------
def feed_buckets(sym: SymbolState, start_ms: int, ratios: list[float], notional: float = 1_000_000) -> None:
    """Write one closed 1m bucket per ratio (aggressive buy share)."""
    from app.state import TradeBucket, minute_bucket

    for i, r in enumerate(ratios):
        key = minute_bucket(start_ms) - (len(ratios) - i)
        b = TradeBucket(notional=notional, buy_notional=notional * r, sell_notional=notional * (1 - r), count=100)
        sym.buckets[key] = b


class TestFlowZScore:
    def test_calm_history_spike_detected(self):
        sym = SymbolState("TESTUSDT")
        now_ms = 1_760_000_000_000
        # 10 prior minutes with mild variation (buy ratio ~50%)
        history = [0.50, 0.48, 0.52, 0.51, 0.49, 0.50, 0.47, 0.51, 0.49, 0.50]
        for i, r in enumerate(history):
            key = minute_bucket(now_ms) - 10 + i
            sym.buckets[key] = TradeBucket(notional=1e6, buy_notional=r * 1e6, sell_notional=(1 - r) * 1e6, count=100)
        # the scored minute: aggressive buyers spike to 90%
        sym.buckets[minute_bucket(now_ms) - 1 + 1] = TradeBucket(notional=1e6, buy_notional=0.9e6, sell_notional=0.1e6, count=100)
        z = aggressive_flow_zscore(sym, now_ms + 60_000)
        assert z > 2.0

    def test_insufficient_history_is_zero(self):
        sym = SymbolState("TESTUSDT")
        assert aggressive_flow_zscore(sym, 0) == 0.0


class TestSymbolStateWindows:
    def test_trade_buckets_and_surge(self):
        sym = SymbolState("TESTUSDT")
        now_ms = 1_760_000_000_000
        now_key = int(now_ms // 60_000)
        # 30 quiet minutes of $1M (keys now-31..now-2)
        for i in range(31, 1, -1):
            sym.buckets[now_key - i] = TradeBucket(notional=1e6)
        # last closed minute: $5M
        sym.buckets[now_key - 1] = TradeBucket(notional=5e6)
        assert sym.volume_1m(now_ms) == pytest.approx(5e6)
        # surge = 5M / mean(35M over 31 buckets)
        assert sym.volume_surge_ratio(now_ms) == pytest.approx(5e6 / (35e6 / 31), rel=0.01)

    def test_taker_buy_ratio(self):
        from app.state import TradeBucket

        sym = SymbolState("TESTUSDT")
        now_ms = 1_760_000_000_000
        sym.buckets[int(now_ms // 60_000) - 1] = TradeBucket(notional=100.0, buy_notional=70.0, sell_notional=30.0)
        assert sym.taker_buy_ratio_1m(now_ms) == pytest.approx(0.7)

    def test_candle_dedup_between_trades_and_kline(self):
        sym = SymbolState("TESTUSDT")
        t = 1_760_000_000_000  # falls inside one minute
        sym.on_trade(t, 100.0, 1.0, False)
        sym.on_trade(t + 1_000, 102.0, 1.0, False)
        # kline for the same minute arrives: must merge, not duplicate
        sym.on_candle(int(t // 60_000) * 60_000, 100.0, 103.0, 99.0, 101.0, 5.0)
        assert len(sym.candles) == 1
        c = sym.candles[-1]
        assert c.high == 103.0 and c.low == 99.0 and c.close == 101.0 and c.volume == 5.0

    def test_price_change_5m(self):
        sym = SymbolState("TESTUSDT")
        now = 1_760_000_000_000
        sym.price_history.append((now - 5 * 60_000 - 500, 100.0))
        sym.price_history.append((now, 110.0))
        sym.price = 110.0
        assert sym.price_change_5m(now) == pytest.approx(10.0)

    def test_bollinger_percentile_bounds(self):
        sym = SymbolState("TESTUSDT")
        t0 = 1_759_000_000_000
        # flat candles -> tight width -> percentile should be low
        for i in range(60):
            sym.on_candle(t0 + i * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0)
        p = sym.bollinger_width_percentile(30)
        assert 0.0 <= p <= 1.0


class TestRefreshWalls:
    def test_walls_map_populated_and_eat_time(self):
        sym = SymbolState("TESTUSDT")
        now_ms = 1_760_000_000_000
        bids = [(100.0, 1000.0)] + [(99.0 - i, 1.0) for i in range(15)]
        asks = [(101.0, 1.0)] + [(102.0 + i, 1.0) for i in range(15)]
        sym.on_book(bids, asks, now_ms)
        # seed level samples so the median is stable
        for _ in range(30):
            sym.on_book(bids, asks, now_ms + 3_000)
        from app.state import TradeBucket

        sym.buckets[int(now_ms // 60_000) - 1] = TradeBucket(notional=3_000_000)
        refresh_walls(sym, multiplier=3.0)
        assert any(w["side"] == "bid" for w in sym.walls.values())
        wall = next(w for w in sym.walls.values() if w["side"] == "bid")
        assert wall["seconds_to_eat"] is not None
        assert wall["seconds_to_eat"] > 0
        assert wall["symbol"] == "TESTUSDT"

    def test_wall_age_survives_refresh_and_resets_after_disappearance(self):
        sym=SymbolState("TESTUSDT"); bids=[(100.0,1000.0)]+[(99.0-i,1.0) for i in range(15)]; asks=[(101.0,1.0)]+[(102.0+i,1.0) for i in range(15)]
        sym.on_book(bids,asks,1_000_000); sym.level_samples.extend([100.0]*100); refresh_walls(sym,3.0)
        first=next(iter(sym.walls.values()))["detected_at"]
        sym.on_book(bids,asks,4_000_000); refresh_walls(sym,3.0)
        same=next(iter(sym.walls.values())); assert same["detected_at"]==first and same["age_s"]==pytest.approx(3000.0)
        sym.on_book([],[],5_000_000); refresh_walls(sym,3.0)
        sym.on_book(bids,asks,6_000_000); refresh_walls(sym,3.0)
        assert next(iter(sym.walls.values()))["detected_at"]==6_000_000
