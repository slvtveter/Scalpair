"""Pydantic v2 schemas shared across the API, streamer and ML engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Wall(BaseModel):
    """A detected density wall (large resting limit order)."""

    symbol: str
    side: Literal["bid", "ask"]
    price: float
    notional_usd: float
    qty: float
    distance_pct: float  # signed: positive = above mid price (ask side)
    seconds_to_eat: float | None = None  # estimated time to consume at recent taker velocity
    detected_at: float  # unix ms


class SymbolMetrics(BaseModel):
    """Rolling quantitative metrics for one asset."""

    symbol: str
    price: float = 0.0
    price_change_5m_pct: float = 0.0
    price_change_24h_pct: float = 0.0
    volume_1m_usd: float = 0.0
    volume_5m_usd: float = 0.0
    volume_surge_ratio: float = 0.0  # V(1m) / mean(V over 30x1m)
    book_imbalance: float = 0.0  # [-1, 1]
    spread_bps: float = 0.0
    depth_notional_usd: float = 0.0  # total top-20 both sides
    trade_velocity_tps: float = 0.0
    taker_buy_ratio_1m: float = 0.5
    bb_width_pctile: float = 0.5  # volatility compression: low = squeezed
    funding_rate: float = 0.0
    open_interest_usd: float = 0.0
    nearest_wall_distance_pct: float | None = None
    # spec indicators (section 4)
    natr_pct: float | None = None          # NATR = 100 × Wilder ATR(14) / close, 1m
    speed_pct_per_min: float | None = None  # signed speed over 60s window
    range_5m_pct: float | None = None       # high-low range over last 5 candles
    surge_closed: float | None = None       # last closed 1m volume / mean of prior 20
    level_dist_pct: float | None = None     # distance to nearest pivot cascade
    level_kind: str | None = None           # "support" | "resistance" | "inside"
    updated_at: float = 0.0  # unix ms


class AiPick(BaseModel):
    """Output of the AI screener for one asset."""

    symbol: str
    scalp_score: float = Field(ge=0, le=100)
    heuristic_score: float = Field(ge=0, le=100)
    anomaly_score: float = Field(ge=0, le=100)
    tag: str
    thesis: str
    metrics: SymbolMetrics
    scored_at: float  # unix ms


class TickerRecord(BaseModel):
    """24h rolling ticker entry used for universe selection."""

    symbol: str
    last_price: float
    quote_volume_24h: float
    price_change_pct_24h: float


class HealthReport(BaseModel):
    status: str
    uptime_s: float
    active_feeds: list[str]
    active_ws_clients: int
    tracked_symbols: int
    messages_ingested: int
    avg_ingest_latency_ms: float
    last_message_age_ms: float | None = None
    redis_connected: bool
    feed_mode: str


class UserOut(BaseModel):
    id: int
    email: str
    subscription_tier: Literal["free", "pro", "whale"] = "free"
    created_at: float


class UserIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    subscription_tier: str
