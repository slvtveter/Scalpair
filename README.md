# ▲ SCALPAIR — AI Crypto Scalping Screener Terminal

A production-ready MVP of a real-time scalping screener & analytics platform inspired by
Scalpboard.io, augmented with an autonomous **AI/ML "Juicy Setup" scoring engine** and a
slonana-inspired brutalist terminal UI.

> **Strictly read-only & analytical.** No private keys, no exchange API secrets,
> no order execution, no custody — ever. 100% free public market data.

```
┌─────────────┐   L2 books     ┌──────────────────────────────┐
│  Binance     │  ───────────▶  │  MarketStreamer               │
│  USDⓈ-M WS   │   aggTrades    │  ┌────────────────────────┐  │
└─────────────┘   klines        │  │ analytics: imbalance,  │  │      ┌────────────┐
                                 │  │ walls, clusters, BB    │  │      │  Redis      │
┌─────────────┐   trades etc.  │  ├────────────────────────┤  │────▶ │ (optional   │
│  Bybit v5    │  ───────────▶  │  │ ml_engine: 12-factor   │  │pubsub│  pub/sub)   │
│  public WS   │   (fallback +  │  │ pipeline → Scalp Score │  │      └────────────┘
└─────────────┘    hybrid)      │  │ (0-100) + tags         │  │
                                 │  └────────────────────────┘  │
┌─────────────┐   offline       │  FastAPI REST + throttled    │
│  MockFeed    │  ───────────▶  │  WebSocket broadcaster       │
└─────────────┘                 └──────────────┬───────────────┘
                                                │ /api/v1/* + /ws
                                 ┌──────────────▼───────────────┐
                                 │  Terminal UI (vanilla JS +   │
                                 │  Canvas, dark, tabular-nums) │
                                 └──────────────────────────────┘
```

## Quick start (one command)

```bash
cp .env.example .env        # optional — sane defaults work out of the box
docker compose up --build
```

Open the terminal at **http://localhost:8080** (nginx) or **http://localhost:8000** (FastAPI
serves the same UI directly). API docs: http://localhost:8000/docs

No keys to configure, nothing to sign up for. The screener boots straight into live data.

## Feed resilience (Binance → Bybit → mock)

`DATA_FEED=auto` (default) probes exchanges in order and degrades gracefully:

1. **Binance USDⓈ-M futures** (`wss://fstream.binance.com`) — primary: `depth20@100ms`,
   `aggTrade`, `kline_1m`, `!ticker@arr`.
2. **Bybit v5 public linear** (`wss://stream.bybit.com/v5/public/linear`) — full fallback:
   `orderbook.50`, `publicTrade`, `kline.1`, `tickers` (funding + OI included).
3. **Hybrid degradation watchdog** — if the primary delivers order books but zero trades
   for 25s (observed on some networks/exchange edges), a supplementary Bybit feed is
   launched for trades/klines/funding while books stay with the primary venue.
4. **MockFeed** — fully offline synthetic market so the terminal always boots, even with no
   internet. Forced via `DATA_FEED=mock`.

All sockets use exponential-backoff reconnection (1.5^n + jitter, capped 60s) and a
staleness watchdog that force-reconnects silent connections.

## Engines

### Engine A — Market Data Streamer (`backend/app/services/streamer.py`)
Tracks the top-N most liquid USDT perpetuals (refreshed every 60s). Per symbol:
- **Bid/Ask imbalance** of top-10 levels: `(Σbids−Σasks)/(Σbids+Σasks)`
- **Density walls**: levels ≥ `WALL_MULTIPLIER` (default 3×) the rolling 5-minute median
  per-level notional, with **distance % from mid** and **seconds-to-eat** at the recent
  5m taker velocity.

### Engine B — AI "Juicy Setup" screener (`backend/app/ml_engine.py`)
12-factor feature vector per asset every 15s (volume surge, book skew, spread, depth,
wall proximity/asymmetry, BB-width squeeze percentile, trade velocity, taker pressure,
aggressive-flow z-score, 5m momentum, funding bps) fused into a **Scalp Alpha Score 0-100**:
- **Deterministic quant heuristic** — works instantly at boot (no training data needed).
- **IsolationForest anomaly detector** — retrained continuously on the rolling feature
  history; blended in (35% weight) once ≥150 samples accumulate.
- Human-readable tags: `Breakout Imminent`, `Absorption at Support`,
  `Orderbook Wall Squeeze`, `Volume Ignition`, `Funding Squeeze`, …

### Engine C — API (`backend/app/api/routes.py`)
| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | feed status, latency, ingest counters, WS clients |
| `GET /api/v1/screeners/densities` | active walls across markets (+distance %) |
| `GET /api/v1/screeners/ai-picks` | top setups by Scalp Score + thesis |
| `GET /api/v1/screeners/overview` | full terminal table payload |
| `GET /api/v1/alerts` | recent ≥85-score alert log |
| `WS /api/v1/ws/live-feed` | unified throttled stream (≤6 updates/s per client) |
| `POST /api/v1/auth/register` / `login`, `GET /me` | JWT + subscription-tier scaffolding |

### Engine D — Terminal UI (`frontend/`)
Vanilla JS + Canvas (zero build step): dense scanner table with tabular-nums mono fonts,
color-coded density meter, density radar, AI hotlist drawer, alert feed with WebAudio ping
(score ≥ 85), click-through price/wall chart modal, score & wall filters.

## SaaS blueprint

Users table ships with `subscription_tier` (`free`/`pro`/`whale`). The WS endpoint
demonstrates freemium gating: anonymous/free clients receive the top-10 symbols with a
throttled update stream; `pro`/`whale` JWTs receive everything at full rate. Telegram bot
webhooks, exports and private API access slot in behind the same auth layer.

## Local development

```bash
cd backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest              # 58 tests: analytics, ML, feeds, API/E2E
./.venv/bin/uvicorn app.main:app --reload # serves the UI at :8000 too
```

Environment knobs (see `.env.example`): `DATA_FEED`, `TOP_SYMBOLS`, `ML_SCORE_INTERVAL`,
`WALL_MULTIPLIER`, `WS_BROADCAST_RATE`, `REDIS_URL`, `DATABASE_URL`, `JWT_SECRET`.

## Safety constraints honored

- Public, unauthenticated exchange endpoints only — no API keys anywhere in the codebase.
- No wallet/signing/execution code exists in this repository.
- All services bind inside the local Docker network; no external telemetry.
