"""Indicator math per Scalpair spec (INDICATORS.md formulas).

All functions are pure and deterministic — fixture-tested in tests/test_indicators.py.
"""

from __future__ import annotations

from app.state import Candle


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def wilder_atr(candles: list[Candle], n: int = 14) -> float | None:
    """Wilder-smoothed ATR over the last `n` candles (needs >= n+1 candles for TR seed)."""
    if len(candles) < n + 1:
        return None
    trs = [
        true_range(candles[i - 1].close, candles[i].high, candles[i].low)
        for i in range(1, len(candles))
    ]
    atr = sum(trs[:n]) / n  # seed = simple mean of first n TRs
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def natr_pct(candles: list[Candle], n: int = 14) -> float | None:
    """NATR% = 100 × ATR(n) / last close; None when data insufficient."""
    atr = wilder_atr(candles, n)
    if atr is None:
        return None
    close = candles[-1].close
    if close <= 0:
        return None
    return round(100.0 * atr / close, 4)


def price_speed_pct_per_min(history: list[tuple[float, float]], window_s: int, now_ms: float) -> float | None:
    """Signed speed %/min over a `window_s` lookback from 1s-sampled price history.

    100 × (P_now / P_then − 1) / Δt_minutes; None when the window has no sample yet.
    """
    if not history or window_s <= 0:
        return None
    now_s = now_ms / 1000
    target = now_s - window_s
    ref = None
    for ts, p in history:
        if ts / 1000 <= target:
            ref = p
        else:
            break
    if ref is None:
        ref = history[0][1]
    if ref <= 0:
        return None
    elapsed_min = max((now_s - history[0][0] / 1000) / 60.0, window_s / 60.0)
    return round(100.0 * (history[-1][1] / ref - 1) / elapsed_min, 4)


def high_low_range_pct(candles: list[Candle], n: int) -> float | None:
    """100 × (max_high − min_low) / min_low over the last `n` candles."""
    if not candles:
        return None
    window = candles[-n:]
    lo = min(c.low for c in window)
    hi = max(c.high for c in window)
    if lo <= 0:
        return None
    return round(100.0 * (hi - lo) / lo, 4)


def volume_surge_closed(candles: list[Candle], n: int = 20) -> float | None:
    """Last CLOSED candle quote-volume / mean volume of the previous N closed candles."""
    closed = candles[:-1] if candles else []
    if len(closed) < n + 1:
        return None
    last = closed[-1].volume
    base = [c.volume for c in closed[-(n + 1) : -1]]
    mean = sum(base) / n
    if mean <= 0:
        return None
    return round(last / mean, 3)
