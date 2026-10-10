"""Pivot levels & cascades per Scalpair spec section 5.

Confirmed swing high/low pivots (k bars each side, non-repainting: the last k
candles can never confirm a pivot), clustered into horizontal price zones
("cascades") of same-direction pivots within a price tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.indicators import wilder_atr
from app.state import Candle


@dataclass
class Pivot:
    kind: str  # "high" | "low"
    index: int  # candle index in the source series
    price: float
    confirmed_at: int  # open_time of the candle that confirmed it (index + k)


@dataclass
class Cascade:
    kind: str  # "support" | "resistance"
    low: float
    high: float
    mid: float
    touches: int
    last_touch_t: int
    pivot_indices: list[int]


def detect_pivots(candles: list[Candle], k: int = 3) -> list[Pivot]:
    """Confirmed swing highs/lows with k completed bars on each side."""
    pivots: list[Pivot] = []
    if k < 1 or len(candles) < 2 * k + 1:
        return pivots
    for i in range(k, len(candles) - k):
        window = candles[i - k : i + k + 1]
        c = candles[i]
        if c.high == max(w.high for w in window) and c.high > max(w.high for w in window[:k] + window[k + 1 :]):
            pivots.append(Pivot("high", i, c.high, candles[i + k].open_time))
        if c.low == min(w.low for w in window) and c.low < min(w.low for w in window[:k] + window[k + 1 :]):
            pivots.append(Pivot("low", i, c.low, candles[i + k].open_time))
    return pivots


def build_cascades(
    candles: list[Candle],
    pivots: list[Pivot],
    *,
    min_touches: int = 3,
    pct_tolerance: float = 0.0015,
    atr_multiplier: float = 0.0,
    min_separation_bars: int = 3,
) -> list[Cascade]:
    """Cluster same-direction pivots into horizontal zones.

    epsilon = max(2×tickSizeApprox, price×pct_tolerance, ATR×atr_multiplier).
    Ties between pivots closer than `min_separation_bars` count as one touch.
    """
    if not pivots or not candles:
        return []
    tick_approx = candles[-1].close * 0.0001
    atr = wilder_atr(candles[-60:], 14) or 0.0

    zones: list[Cascade] = []
    for kind, zkind in (("high", "resistance"), ("low", "support")):
        pts = sorted((p for p in pivots if p.kind == kind), key=lambda p: p.index)
        cluster: list[Pivot] = []
        for p in pts:
            if not cluster:
                cluster = [p]
                continue
            anchor_price = sum(x.price for x in cluster) / len(cluster)
            epsilon = max(
                2 * tick_approx,
                anchor_price * pct_tolerance,
                atr * atr_multiplier,
            )
            separated = p.index - cluster[-1].index >= min_separation_bars
            if abs(p.price - anchor_price) <= epsilon:
                if separated:
                    cluster.append(p)
                else:  # same swing re-detected — keep the more extreme value
                    if (kind == "high" and p.price > cluster[-1].price) or (
                        kind == "low" and p.price < cluster[-1].price
                    ):
                        cluster[-1] = p
            else:
                zones.append(_make_zone(zkind, cluster))
                cluster = [p]
        if cluster:
            zones.append(_make_zone(zkind, cluster))
    zones = [z for z in zones if z.touches >= min_touches]
    # resolve S/R conflicts: overlapping opposite-kind zones contradict each other;
    # keep the stronger (more touches), drop the weaker.
    kept: list[Cascade] = []
    for z in sorted(zones, key=lambda z: z.touches, reverse=True):
        conflict = False
        for k, o in ((x.kind, x) for x in kept):
            if k == z.kind:
                continue
            overlap = min(z.high, o.high) - max(z.low, o.low)
            smaller = min(z.high - z.low, o.high - o.low)
            # drop the weaker zone only when mostly swallowed by the opposite one
            if overlap > 0 and overlap / smaller > 0.6:
                conflict = True
                break
        if conflict:
            continue
        kept.append(z)
    # readability: merge zones narrower than 0.05% of price into the same-kind
    # neighbour, then cap the count (the chart draws only the strongest few)
    price = candles[-1].close
    min_width = price * 0.0005
    merged: list[Cascade] = []
    for z in sorted(kept, key=lambda z: z.mid):
        if merged and z.mid - merged[-1].mid < min_width and z.kind == merged[-1].kind:
            a, b = merged[-1], z
            lo, hi = min(a.low, b.low), max(a.high, b.high)
            merged[-1] = Cascade(
                kind=a.kind, low=lo, high=hi, mid=(lo + hi) / 2,
                touches=a.touches + b.touches,
                last_touch_t=max(a.last_touch_t, b.last_touch_t),
                pivot_indices=a.pivot_indices + b.pivot_indices,
            )
        else:
            merged.append(z)
    return sorted(merged, key=lambda z: z.touches, reverse=True)[:8]


def _make_zone(zkind: str, cluster: list[Pivot]) -> Cascade:
    prices = [p.price for p in cluster]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.1 or lo * 0.0001
    return Cascade(
        kind=zkind,
        low=round(lo - pad, 8),
        high=round(hi + pad, 8),
        mid=round((lo + hi) / 2, 8),
        touches=len(cluster),
        last_touch_t=max(p.confirmed_at for p in cluster),
        pivot_indices=[p.index for p in cluster],
    )


def nearest_cascade_distance_pct(cascades: list[Cascade], price: float) -> tuple[float | None, Cascade | None]:
    """(distance %, zone) of the nearest cascade to `price`; None when no zones."""
    best: tuple[float, Cascade] | None = None
    for z in cascades:
        if z.low <= price <= z.high:
            d = 0.0
        else:
            d = min(abs(price - z.low), abs(price - z.high)) / price * 100.0
        if best is None or d < best[0]:
            best = (d, z)
    return (round(best[0], 4), best[1]) if best else (None, None)
