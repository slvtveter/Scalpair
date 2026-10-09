"""Quantitative analytics: order-book imbalance, density/wall detection,
trade-cluster (absorption) heuristics and rolling metric computation.

All functions are pure where possible so they can be unit-tested without
network or event-loop machinery.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.models import SymbolMetrics
from app.state import MarketState, SymbolState

log = logging.getLogger("scalpair.analytics")

# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


def imbalance_ratio(bids: list[tuple[float, float]], asks: list[tuple[float, float]], levels: int = 10) -> float:
    """Bid/Ask imbalance of the top `levels` book levels.

    (ΣBids - ΣAsks) / (ΣBids + ΣAsks)  ∈ [-1, 1];  +1 = full bid stack.
    """
    bid_sum = sum(q for _, q in bids[:levels])
    ask_sum = sum(q for _, q in asks[:levels])
    denom = bid_sum + ask_sum
    if denom <= 0:
        return 0.0
    return (bid_sum - ask_sum) / denom


def detect_walls(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    median_level_notional: float,
    multiplier: float,
    max_levels: int = 20,
) -> list[dict[str, Any]]:
    """Detect density walls: levels whose notional is >= multiplier × median.

    Returns a list of {side, price, qty, notional_usd, distance_pct}.
    """
    if median_level_notional <= 0:
        return []
    mids = []
    if bids and asks:
        mids = [(bids[0][0] + asks[0][0]) / 2]
    elif bids:
        mids = [bids[0][0]]
    elif asks:
        mids = [asks[0][0]]
    if not mids:
        return []
    mid = mids[0]
    if mid <= 0:
        return []

    threshold = multiplier * median_level_notional
    walls: list[dict[str, Any]] = []
    for side, levels in (("bid", bids), ("ask", asks)):
        for price, qty in levels[:max_levels]:
            notional = price * qty
            if notional < threshold:
                continue
            distance_pct = (price - mid) / mid * 100.0
            walls.append(
                {
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "notional_usd": notional,
                    "distance_pct": round(distance_pct, 4),
                }
            )
    walls.sort(key=lambda w: w["notional_usd"], reverse=True)
    return walls


def aggressive_flow_zscore(state: SymbolState, now_ms: float) -> float:
    """Z-score of the last-minute aggressive taker-buy ratio vs prior 10 minutes.

    High positive => aggressive buyers dominating vs their recent norm
    (accumulation); strongly negative => distribution.
    """
    prior = state.bucket_range(now_ms, 11)  # includes the just-closed minute
    if len(prior) < 6:
        return 0.0
    ratios = []
    for b in prior[:-1]:
        if b.notional > 0:
            ratios.append(b.buy_notional / b.notional)
    if len(ratios) < 5:
        return 0.0
    cur = prior[-1]
    if cur.notional <= 0:
        return 0.0
    cur_ratio = cur.buy_notional / cur.notional
    arr = np.asarray(ratios, dtype=float)
    std = float(arr.std())
    if std < 1e-9:
        return 0.0
    return float((cur_ratio - arr.mean()) / std)


def seconds_to_eat_wall(notional_usd: float, volume_5m_usd: float) -> float | None:
    """Estimate seconds required to consume a wall at the recent 5m taker velocity."""
    if volume_5m_usd <= 0:
        return None
    velocity = volume_5m_usd / 300.0  # usd per second
    if velocity <= 0:
        return None
    return round(notional_usd / velocity, 1)


# ---------------------------------------------------------------------------
# Full metric computation
# ---------------------------------------------------------------------------


def compute_metrics(state: MarketState, sym_state: SymbolState, now_ms: float) -> SymbolMetrics:
    """Fill the rolling SymbolMetrics snapshot for one asset."""
    m = sym_state.metrics
    m.symbol = sym_state.symbol
    m.price = sym_state.price
    m.price_change_5m_pct = round(sym_state.price_change_5m(now_ms), 4)
    m.price_change_24h_pct = round(sym_state.price_change_24h_pct, 4)
    m.volume_1m_usd = round(sym_state.volume_1m(now_ms), 2)
    m.volume_5m_usd = round(sym_state.volume_5m(now_ms), 2)
    m.volume_surge_ratio = round(sym_state.volume_surge_ratio(now_ms), 3)
    m.book_imbalance = round(sym_state.imbalance(10), 4)
    m.spread_bps = round(sym_state.spread_bps(), 3)
    m.depth_notional_usd = round(sym_state.depth_notional(20), 2)
    m.trade_velocity_tps = round(sym_state.trade_velocity_tps(now_ms), 2)
    m.taker_buy_ratio_1m = round(sym_state.taker_buy_ratio_1m(now_ms), 4)
    m.bb_width_pctile = round(sym_state.bollinger_width_percentile(30), 4)
    m.funding_rate = sym_state.funding_rate
    m.open_interest_usd = sym_state.open_interest_usd
    m.updated_at = now_ms

    # spec indicators (section 4) — candle-based
    from app.indicators import high_low_range_pct, natr_pct, price_speed_pct_per_min, volume_surge_closed
    from app.levels import nearest_cascade_distance_pct

    m.natr_pct = natr_pct(list(sym_state.candles), 14)
    m.speed_pct_per_min = price_speed_pct_per_min(list(sym_state.price_history), 60, now_ms)
    m.range_5m_pct = high_low_range_pct(list(sym_state.candles), 5)
    m.surge_closed = volume_surge_closed(list(sym_state.candles), 20)
    if sym_state.cascades and sym_state.price > 0:
        dist, zone = nearest_cascade_distance_pct(sym_state.cascades, sym_state.price)
        m.level_dist_pct = dist
        if zone is not None:
            m.level_kind = "inside" if dist == 0.0 else zone.kind
    else:
        m.level_dist_pct = None
        m.level_kind = None

    # nearest wall distance (walls are refreshed by the streamer before metrics)
    if sym_state.walls:
        dists = [abs(w["distance_pct"]) for w in sym_state.walls.values()]
        m.nearest_wall_distance_pct = round(min(dists), 4)
    else:
        m.nearest_wall_distance_pct = None
    return m


def refresh_walls(sym_state: SymbolState, multiplier: float) -> None:
    """Recompute the wall map for one symbol from its latest book snapshot."""
    if not sym_state.bids or not sym_state.asks:
        return
    median = sym_state.median_level_notional()
    found = detect_walls(sym_state.bids, sym_state.asks, median, multiplier)
    now_ms = sym_state.book_ts_ms or 0
    new_map: dict[tuple[str, float], dict] = {}
    v5m = sym_state.volume_5m(now_ms)
    for w in found:
        key = (w["side"], round(w["price"], 8))
        w["seconds_to_eat"] = seconds_to_eat_wall(w["notional_usd"], v5m)
        w["symbol"] = sym_state.symbol
        w["detected_at"] = now_ms
        new_map[key] = w
    sym_state.walls = new_map
