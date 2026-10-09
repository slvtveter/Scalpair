"""Unit tests for the ML scoring engine: features, heuristic, anomaly model."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml_engine import (
    AnomalyModel,
    FEATURE_NAMES,
    ScalpScorer,
    extract_features,
    heuristic_score,
)
from app.state import MarketState, SymbolState, TradeBucket, minute_bucket


def make_hot_symbol(symbol: str = "PUMPUSDT") -> SymbolState:
    """A symbol with a strong breakout-adjacent profile."""
    sym = SymbolState(symbol)
    now_ms = 1_760_000_000_000
    now_key = minute_bucket(now_ms)
    # 30 quiet minutes
    for i in range(2, 32):
        sym.buckets[now_key - i] = TradeBucket(notional=1_000_000, buy_notional=500_000, sell_notional=500_000, count=50)
    # explosive last minute: 6x volume, aggressive buying
    sym.buckets[now_key - 1] = TradeBucket(notional=6_000_000, buy_notional=4_500_000, sell_notional=1_500_000, count=900)
    # tight candles (squeeze) then flat
    t0 = now_ms - 40 * 60_000
    for i in range(38):
        sym.on_candle(t0 + i * 60_000, 100.0, 100.4, 99.6, 100.0, 1000.0)
    sym.price = 100.0
    sym.price_history.append((now_ms - 5 * 60_000, 100.0))
    sym.price_history.append((now_ms, 100.2))
    bids = [(99.9, 50_000.0)] + [(99.8 - i, 1.0) for i in range(15)]
    asks = [(100.1, 1.0)] + [(100.2 + i, 1.0) for i in range(15)]
    for k in range(30):
        sym.on_book(bids, asks, now_ms - 60_000 + k * 3_000)
    from app.analytics import refresh_walls

    sym.metrics.funding_rate = 0.0001
    refresh_walls(sym, multiplier=3.0)
    from app.analytics import compute_metrics

    compute_metrics(MarketState(), sym, now_ms)
    return sym


def make_dead_symbol(symbol: str = "DEADUSDT") -> SymbolState:
    sym = SymbolState(symbol)
    sym.price = 100.0
    return sym


class TestExtractFeatures:
    def test_shape_and_names(self):
        sym = make_hot_symbol()
        f = extract_features(sym, 1_760_000_000_000)
        assert f.shape == (12,)
        assert len(FEATURE_NAMES) == 12

    def test_values_finite_and_bounded(self):
        for sym in (make_hot_symbol(), make_dead_symbol()):
            f = extract_features(sym, 1_760_000_000_000)
            assert np.isfinite(f).all()
            assert -50 <= f[11] <= 50      # funding bps clipped
            assert 0 <= f[6] <= 1          # bb compression
            assert -1 <= f[1] <= 1         # imbalance


class TestHeuristicScore:
    def test_score_range(self):
        for sym in (make_hot_symbol(), make_dead_symbol()):
            f = extract_features(sym, 1_760_000_000_000)
            score, tag, thesis = heuristic_score(f, sym, 1_760_000_000_000)
            assert 0 <= score <= 100
            assert tag
            assert thesis

    def test_hot_symbol_scores_higher_than_dead(self):
        now = 1_760_000_000_000
        fh = extract_features(make_hot_symbol(), now)
        fd = extract_features(make_dead_symbol(), now)
        hot = heuristic_score(fh, make_hot_symbol(), now)
        dead = heuristic_score(fd, make_dead_symbol(), now)
        assert hot[0] > dead[0] + 20

    def test_squeeze_breakout_tag(self):
        sym = make_hot_symbol()
        f = extract_features(sym, 1_760_000_000_000)
        # force surge + compression conditions
        f[0] = 5.0
        f[6] = 0.9
        _, tag, _ = heuristic_score(f, sym, 1_760_000_000_000)
        assert tag == "Breakout Imminent"

    def test_quiet_tag_for_dead(self):
        f = extract_features(make_dead_symbol(), 1_760_000_000_000)
        _, tag, _ = heuristic_score(f, make_dead_symbol(), 1_760_000_000_000)
        assert tag == "Quiet — Watch"


class TestAnomalyModel:
    def test_not_ready_returns_zero(self):
        model = AnomalyModel()
        assert model.anomaly_score(np.zeros(12)) == 0.0

    def test_outlier_scores_higher_after_fit(self):
        rng = np.random.default_rng(42)
        model = AnomalyModel()
        inliers = rng.normal(0, 0.1, size=(250, 12))
        for row in inliers:
            model.observe(row)
        assert model.ready
        outlier = np.full(12, 8.0)
        normal = rng.normal(0, 0.1, size=12)
        assert model.anomaly_score(outlier) > model.anomaly_score(normal)


class TestScalpScorer:
    def test_score_symbol_produces_pick(self):
        scorer = ScalpScorer()
        state = MarketState()
        state.symbols["PUMPUSDT"] = make_hot_symbol()
        pick = scorer.score_symbol(state, "PUMPUSDT", 1_760_000_000_000)
        assert pick is not None
        assert 0 <= pick.scalp_score <= 100
        assert pick.tag
        assert pick.metrics.symbol == "PUMPUSDT"

    def test_top_ranking(self):
        scorer = ScalpScorer()
        state = MarketState()
        state.symbols["PUMPUSDT"] = make_hot_symbol("PUMPUSDT")
        state.symbols["DEADUSDT"] = make_dead_symbol("DEADUSDT")
        now = 1_760_000_000_000
        scorer.score_symbol(state, "PUMPUSDT", now)
        scorer.score_symbol(state, "DEADUSDT", now)
        top = scorer.top(2)
        assert top[0].symbol == "PUMPUSDT"

    def test_missing_symbol_returns_none(self):
        scorer = ScalpScorer()
        assert scorer.score_symbol(MarketState(), "NOPEUSDT") is None
