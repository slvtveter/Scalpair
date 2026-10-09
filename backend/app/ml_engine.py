"""AI "Juicy Setup" scoring engine.

Produces a Scalp Alpha Score (0-100) per asset every tick by fusing:

1. A deterministic, quant-weighted heuristic score (works instantly at boot,
   no training data required).
2. An IsolationForest anomaly detector trained continuously on the rolling
   12-factor feature history (blended in once enough samples accumulate).

Feature vector (12 factors):
  0 volume_surge      V(1m) / mean(V over 30x1m)            clipped [0, 10]
  1 book_imbalance    top-10 level imbalance                [-1, 1]
  2 spread_bps        bid-ask spread in basis points        clipped [0, 50]
  3 depth_log         log10 of top-20 depth notional (USD)
  4 wall_proximity    1 - |dist to nearest wall| / 2%       [0, 1]
  5 wall_asymmetry    (bidWalls - askWalls) / total         [-1, 1]
  6 bb_compression    1 - BB width percentile               [0, 1]
  7 trade_velocity    log1p(trades per second)
  8 taker_pressure    2 * (takerBuyRatio - 0.5)             [-1, 1]
  9 flow_zscore       aggressive-flow z vs prior 10 min     clipped [-5, 5]
 10 price_mom_5m      5-minute return %                     clipped [-5, 5]
 11 funding_bps       funding rate in bps per interval      clipped [-20, 20]
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

import numpy as np

from app.analytics import aggressive_flow_zscore
from app.models import AiPick
from app.state import MarketState, SymbolState

log = logging.getLogger("scalpair.ml")

FEATURE_NAMES = [
    "volume_surge",
    "book_imbalance",
    "spread_bps",
    "depth_log",
    "wall_proximity",
    "wall_asymmetry",
    "bb_compression",
    "trade_velocity",
    "taker_pressure",
    "flow_zscore",
    "price_mom_5m",
    "funding_bps",
]


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def extract_features(sym: SymbolState, now_ms: float) -> np.ndarray:
    """Compute the normalized 12-factor vector for one asset."""
    m = sym.metrics
    wall_notional = {"bid": 0.0, "ask": 0.0}
    for w in sym.walls.values():
        wall_notional[w["side"]] += w["notional_usd"]
    total_wall = wall_notional["bid"] + wall_notional["ask"]
    asym = 0.0 if total_wall <= 0 else (wall_notional["bid"] - wall_notional["ask"]) / total_wall
    proximity = 0.0
    if m.nearest_wall_distance_pct is not None:
        proximity = _clip(1.0 - abs(m.nearest_wall_distance_pct) / 2.0, 0.0, 1.0)

    return np.array(
        [
            _clip(m.volume_surge_ratio, 0.0, 10.0),
            m.book_imbalance,
            _clip(m.spread_bps, 0.0, 50.0),
            float(np.log10(max(m.depth_notional_usd, 1.0) + 1.0)),
            proximity,
            asym,
            _clip(1.0 - m.bb_width_pctile, 0.0, 1.0),
            float(np.log1p(m.trade_velocity_tps)),
            _clip((m.taker_buy_ratio_1m - 0.5) * 2.0, -1.0, 1.0),
            _clip(aggressive_flow_zscore(sym, now_ms), -5.0, 5.0),
            _clip(m.price_change_5m_pct, -5.0, 5.0),
            _clip(m.funding_rate * 10_000.0, -20.0, 20.0),
        ],
        dtype=float,
    )


# ---------------------------------------------------------------------------
# Deterministic heuristic scorer (instant bootstrap, no training needed)
# ---------------------------------------------------------------------------

TAG_RULES_PRIORITY = [
    "Breakout Imminent",
    "Absorption at Support",
    "Distribution at Resistance",
    "Orderbook Wall Squeeze",
    "Volume Ignition",
    "Funding Squeeze",
    "High Momentum",
    "Aggressive Buying",
    "Aggressive Selling",
    "Quiet — Watch",
]


def heuristic_score(f: np.ndarray, sym: SymbolState, now_ms: float) -> tuple[float, str, str]:
    """Rule-weighted 0-100 score + tag + human-readable thesis."""
    surge, imb = f[0], f[1]
    spread, depth = f[2], f[3]
    proximity, asym = f[4], f[5]
    squeeze, velocity = f[6], f[7]
    taker, z = f[8], f[9]
    mom, funding = f[10], f[11]

    # -- weighted contribution terms (sum ≈ 100 at extremes) ----------------
    s_surge = _clip(13.0 * np.log2(1.0 + surge), 0.0, 40.0)
    s_imb = 10.0 * abs(imb)
    s_squeeze = 15.0 * squeeze
    s_velocity = _clip(10.0 * velocity / np.log1p(50.0), 0.0, 10.0)
    s_flow = 10.0 * _clip(abs(z) / 3.0, 0.0, 1.0)
    s_wall = 10.0 * proximity
    s_taker = 8.0 * abs(taker)
    s_mom = 7.0 * _clip(abs(mom) / 1.5, 0.0, 1.0)
    raw = s_surge + s_imb + s_squeeze + s_velocity + s_flow + s_wall + s_taker + s_mom
    score = _clip(raw, 0.0, 100.0)

    # -- tag selection (first rule hit wins) --------------------------------
    tag = "Quiet — Watch"
    if surge > 3.0 and squeeze > 0.65:
        tag = "Breakout Imminent"
    elif z > 1.5 and proximity > 0.4 and asym >= 0:
        tag = "Absorption at Support"
    elif z < -1.5 and proximity > 0.4 and asym <= 0:
        tag = "Distribution at Resistance"
    elif proximity > 0.5 and abs(asym) > 0.45:
        tag = "Orderbook Wall Squeeze"
    elif surge > 2.5:
        tag = "Volume Ignition"
    elif abs(funding) > 8.0:
        tag = "Funding Squeeze"
    elif abs(mom) > 1.0:
        tag = "High Momentum"
    elif taker > 0.4:
        tag = "Aggressive Buying"
    elif taker < -0.4:
        tag = "Aggressive Selling"

    thesis = _build_thesis(sym, now_ms, surge, imb, squeeze, z, proximity, asym, taker, mom, funding, tag)
    return round(score, 1), tag, thesis


def _build_thesis(
    sym: SymbolState, now_ms: float, surge: float, imb: float, squeeze: float, z: float,
    proximity: float, asym: float, taker: float, mom: float, funding: float, tag: str,
) -> str:
    parts: list[str] = []
    if surge >= 1.5:
        parts.append(f"1m volume {surge:.1f}x above 30m norm")
    if abs(imb) > 0.25:
        side = "bid" if imb > 0 else "ask"
        parts.append(f"book skewing {side} ({imb:+.2f})")
    if squeeze > 0.7:
        parts.append("volatility compressed (BB squeeze)")
    if proximity > 0.3 and sym.walls:
        best = max(sym.walls.values(), key=lambda w: w["notional_usd"])
        eat = best.get("seconds_to_eat")
        eat_txt = f", ~{eat:.0f}s to eat" if eat else ""
        parts.append(
            f"{best['notional_usd'] / 1e6:.1f}M {best['side']} wall {abs(best['distance_pct']):.2f}% away{eat_txt}"
        )
    if abs(z) > 1.5:
        parts.append(f"aggressive {'buyers' if z > 0 else 'sellers'} {abs(z):.1f}σ vs norm")
    if abs(mom) > 0.8:
        parts.append(f"5m move {mom:+.2f}%")
    if abs(funding) > 8.0:
        parts.append(f"funding {funding:+.1f}bp")
    if not parts:
        parts.append("no dominant edge — monitor for ignition")
    return f"{tag}: " + "; ".join(parts[:4]) + "."


# ---------------------------------------------------------------------------
# IsolationForest anomaly layer (blended in once warm)
# ---------------------------------------------------------------------------


class AnomalyModel:
    """Continuously-trained IsolationForest over the global feature space."""

    MIN_SAMPLES = 150
    RETRAIN_EVERY_S = 300.0
    BUFFER_LEN = 3000

    def __init__(self) -> None:
        self.buffer: deque[np.ndarray] = deque(maxlen=self.BUFFER_LEN)
        self._model: Any = None
        self._decision_history: deque[float] = deque(maxlen=self.BUFFER_LEN)
        self._last_fit_at = 0.0
        self._new_since_fit = 0
        self.ready = False

    def observe(self, features: np.ndarray) -> None:
        self.buffer.append(features)
        self._new_since_fit += 1
        if self.ready and self._model is not None:
            decision = float(self._model.decision_function(features.reshape(1, -1))[0])
            self._decision_history.append(-decision)
        should_fit = (
            len(self.buffer) >= self.MIN_SAMPLES
            and self._new_since_fit >= 25
            and time.time() - self._last_fit_at >= 30.0
        )
        if should_fit:
            self._fit()

    def _fit(self) -> None:
        from sklearn.ensemble import IsolationForest  # local import: lazy, keeps boot fast

        X = np.vstack(list(self.buffer))
        try:
            model = IsolationForest(n_estimators=100, random_state=42, n_jobs=-1)
            model.fit(X)
            self._model = model
            for d in -model.decision_function(X):
                self._decision_history.append(float(d))
            self._last_fit_at = time.time()
            self._new_since_fit = 0
            self.ready = True
            log.info("anomaly model fit on %d samples", len(X))
        except Exception:  # noqa: BLE001 — ML must never kill the pipeline
            log.exception("anomaly model fit failed")

    def anomaly_score(self, features: np.ndarray) -> float:
        """0-100 percentile of unusualness (0 = totally normal)."""
        if not self.ready or self._model is None or len(self._decision_history) < 50:
            return 0.0
        decision = float(self._model.decision_function(features.reshape(1, -1))[0])
        val = -decision
        hist = np.fromiter(self._decision_history, dtype=float, count=len(self._decision_history))
        return round(float((hist <= val).mean() * 100.0), 1)


# ---------------------------------------------------------------------------
# Orchestrating scorer
# ---------------------------------------------------------------------------


class ScalpScorer:
    """Blends heuristic + anomaly into the final Scalp Alpha Score."""

    def __init__(self, heuristic_weight: float = 0.65) -> None:
        self.heuristic_weight = _clip(heuristic_weight, 0.0, 1.0)
        self.anomaly = AnomalyModel()
        self.picks: dict[str, AiPick] = {}
        self.last_tick = 0.0

    def score_symbol(self, state: MarketState, symbol: str, now_ms: float | None = None) -> AiPick | None:
        sym = state.symbols.get(symbol)
        if sym is None or sym.price <= 0:
            return None
        now_ms = now_ms or (time.time() * 1000)
        features = extract_features(sym, now_ms)
        self.anomaly.observe(features)
        h_score, tag, thesis = heuristic_score(features, sym, now_ms)
        a_score = self.anomaly.anomaly_score(features)
        final = _clip(
            self.heuristic_weight * h_score + (1.0 - self.heuristic_weight) * a_score * 0.9,
            0.0,
            100.0,
        )
        pick = AiPick(
            symbol=symbol,
            scalp_score=round(final, 1),
            heuristic_score=h_score,
            anomaly_score=a_score,
            tag=tag,
            thesis=thesis,
            metrics=sym.metrics.model_copy(deep=True),
            scored_at=now_ms,
        )
        self.picks[symbol] = pick
        return pick

    def top(self, n: int = 5, min_score: float = 0.0) -> list[AiPick]:
        ranked = sorted(self.picks.values(), key=lambda p: p.scalp_score, reverse=True)
        return [p for p in ranked if p.scalp_score >= min_score][:n]

    def all_picks(self) -> list[AiPick]:
        return sorted(self.picks.values(), key=lambda p: p.scalp_score, reverse=True)
