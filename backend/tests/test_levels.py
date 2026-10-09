"""Deterministic pivot/cascade tests (spec acceptance #4)."""

from __future__ import annotations

from app.levels import build_cascades, detect_pivots, nearest_cascade_distance_pct
from app.state import Candle


def mk(closes: list[float], spread: float = 0.2) -> list[Candle]:
    t0 = 1_760_000_000_000
    return [Candle(t0 + i * 60_000, c - spread / 2, c + spread / 2, c - spread / 2, c, 10.0) for i, c in enumerate(closes)]


def test_k3_pivot_not_confirmed_before_future_bars():
    """A spike at the very end cannot be a confirmed pivot (needs k future bars)."""
    closes = [100.0] * 10 + [110.0]  # spike is the last candle
    pivots = detect_pivots(mk(closes), k=3)
    assert all(p.index != len(closes) - 1 for p in pivots)


def test_clear_swing_high_confirmed_after_k_bars():
    closes = [100, 100, 100, 110, 100, 100, 100, 100, 100, 100]
    pivots = detect_pivots(mk([float(c) for c in closes]), k=3)
    highs = [p for p in pivots if p.kind == "high"]
    assert len(highs) == 1
    assert highs[0].index == 3
    assert highs[0].confirmed_at == 1_760_000_000_000 + 6 * 60_000  # index 3 + k=3


def test_double_top_single_pivot_strict():
    closes = [100, 100, 110, 100, 110, 100, 100, 100, 100]
    pivots = detect_pivots(mk([float(c) for c in closes]), k=3)
    # two equal tops 2 bars apart — strict comparison: only distinct swings count;
    # with k=3 the window [i-3..i+3] around each top includes the other equal top,
    # so neither is strictly greater -> no high pivot from the flat double top.
    highs = [p for p in pivots if p.kind == "high"]
    assert highs == [] or all(p.price == 110.0 for p in highs)


def test_three_touches_build_zone():
    # swing highs at ~110 at indices 3, 7, 11 — each with k=3 clean bars both sides
    closes = [100, 100, 100, 110, 100, 100, 100, 110, 100, 100, 100, 110, 100, 100, 100, 100]
    candles = mk([float(c) for c in closes])
    pivots = detect_pivots(candles, k=3)
    zones = build_cascades(candles, pivots, min_touches=3)
    resist = [z for z in zones if z.kind == "resistance"]
    assert len(resist) == 1
    assert resist[0].touches == 3
    # zone is built around the swing-high prices (close + spread/2 = 110.1)
    assert resist[0].low <= 110.1 <= resist[0].high


def test_min_touches_one_reveals_single_extrema():
    closes = [100, 100, 100, 110, 100, 100, 100, 100, 100]
    candles = mk([float(c) for c in closes])
    pivots = detect_pivots(candles, k=3)
    zones = build_cascades(candles, pivots, min_touches=1)
    assert any(z.kind == "resistance" and z.touches == 1 for z in zones)


def test_nearest_cascade_distance():
    closes = [100, 100, 100, 110, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
    candles = mk([float(c) for c in closes])
    pivots = detect_pivots(candles, k=3)
    zones = build_cascades(candles, pivots, min_touches=1)
    d, z = nearest_cascade_distance_pct(zones, 105.0)
    assert d is not None and d >= 0
    assert z is not None
    d2, _ = nearest_cascade_distance_pct([], 100.0)
    assert d2 is None
