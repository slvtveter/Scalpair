"""Deterministic fixture tests for indicator formulas (spec section 4)."""

from __future__ import annotations

import pytest

from app.indicators import (
    high_low_range_pct,
    natr_pct,
    price_speed_pct_per_min,
    true_range,
    volume_surge_closed,
    wilder_atr,
)
from app.state import Candle, SymbolState


def mk_candles(closes: list[float], spread: float = 0.5) -> list[Candle]:
    out = []
    t0 = 1_760_000_000_000
    for i, c in enumerate(closes):
        out.append(Candle(t0 + i * 60_000, c - spread / 2, c + spread / 2, c - spread / 2, c, 10.0))
    return out


class TestTrueRangeAndATR:
    def test_true_range_cases(self):
        assert true_range(prev_close=100, high=105, low=101) == pytest.approx(5.0)  # H-L
        assert true_range(prev_close=103, high=105, low=101) == pytest.approx(4.0)  # |H-prevC|
        assert true_range(prev_close=98, high=105, low=101) == pytest.approx(7.0)   # |H-prevC| dominates

    def test_flat_market_atr_equals_spread(self):
        candles = mk_candles([100.0] * 30)
        assert wilder_atr(candles, 14) == pytest.approx(0.5)

    def test_natr_flat_market(self):
        candles = mk_candles([100.0] * 30)
        assert natr_pct(candles, 14) == pytest.approx(0.5)  # 0.5/100*100

    def test_insufficient_data_returns_none(self):
        assert wilder_atr(mk_candles([100.0] * 5), 14) is None
        assert natr_pct(mk_candles([100.0] * 5), 14) is None

    def test_wilder_smoothing_is_recursive(self):
        # ATR must converge toward the running TR mean, weighted toward history
        closes = [100.0] * 20 + [110.0] * 5  # step jump raises TR
        candles = mk_candles(closes)
        atr = wilder_atr(candles, 14)
        assert 0.5 < atr < 11.0  # partial adjustment, not full jump


class TestPriceSpeed:
    def test_signed_speed(self):
        now_ms = 1_760_000_000_000
        hist = [(now_ms - 60_000, 100.0), (now_ms, 101.0)]  # +1% over 1m
        assert price_speed_pct_per_min(hist, 60, now_ms) == pytest.approx(1.0)

    def test_negative_speed(self):
        now_ms = 1_760_000_000_000
        hist = [(now_ms - 30_000, 100.0), (now_ms, 99.5)]
        v = price_speed_pct_per_min(hist, 30, now_ms)
        assert v < 0

    def test_no_history_none(self):
        assert price_speed_pct_per_min([], 60, 0) is None


class TestRangeAndSurge:
    def test_range(self):
        candles = mk_candles([100.0, 101.0, 99.0, 102.0])
        r = high_low_range_pct(candles, 4)
        assert r == pytest.approx((102.25 - 98.75) / 98.75 * 100, rel=0.01)

    def test_surge_spike(self):
        closes = [100.0] * 25
        candles = mk_candles(closes)
        for c in candles:
            c.volume = 10.0
        candles[-2].volume = 30.0  # last CLOSED candle = 3x mean of the prior 20 closed
        assert volume_surge_closed(candles, 20) == pytest.approx(3.0)

    def test_surge_insufficient(self):
        assert volume_surge_closed(mk_candles([100.0] * 5), 20) is None
