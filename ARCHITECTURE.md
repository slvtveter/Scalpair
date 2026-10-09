# Scalpair — Architecture

## Data flow

```
Binance USDⓈ-M WS ──┐                         ┌─ analytics (walls, imbalance, indicators)
Bybit v5 linear WS ─┼─→ FeedConnector ────────→│─→ MarketState (in-memory, single event loop)
MockFeed (offline) ─┘   (reconnect/backoff,     ├─→ levels engine (pivots/cascades, 30s cadence)
                        staleness watchdog,     ├─→ ML scorer (12-factor + IsolationForest, 15s)
                        hybrid degradation,     ├─→ alert engine (2s, edge semantics + cooldown)
                        live recovery loop)     └─→ broadcaster ─→ WS /api/v1/ws/live-feed (≤6 msg/s, tiered)
                                                        │
                                              REST /api/v1/* (FastAPI, pydantic v2)
                                                        │
                                              SQLite WAL (users, rules, events, deliveries)
                                                        │
                                     frontend: vanilla JS terminal (table / board 3×3 / chart modal)
```

## Modules

| Path | Responsibility |
|---|---|
| `app/feeds/{base,binance,bybit,mock}.py` | venue adapters, normalized callbacks, reconnection, seq validation + resync |
| `app/state.py` | in-memory per-symbol state: book, trade buckets, candles, walls, pivots, metrics |
| `app/analytics.py` | wall detection, imbalance, metric computation |
| `app/indicators.py` | NATR, speed, range, surge (spec formulas) |
| `app/levels.py` | confirmed pivots (k=3) and cascade zones |
| `app/ml_engine.py` | 12-factor feature vector, heuristic scorer, IsolationForest blend |
| `app/alerts.py` | server-side rules engine, edge semantics, cooldown/re-arm, durable deliveries |
| `app/services/{streamer,broadcaster}.py` | orchestration loops; throttled WS fanout with per-client bounded queues |
| `app/api/{routes,auth,alerts_api,ratelimit}.py` | REST + WS endpoints, JWT, CRUD, rate limits |
| `app/db.py` | SQLAlchemy async, SQLite WAL (swap URL for Postgres) |
| `frontend/` | vanilla JS terminal: scanner table, sparklines, board grid, chart modal, i18n RU/EN |

## Deliberate deviations from the reference stack

The spec suggests React/TS/Vite + lightweight-charts + Postgres/Alembic. The delivered
P1 keeps a zero-build vanilla JS frontend and SQLite to ship a working slice fast; the
API contracts are stable, so a staged migration is possible without touching the data
pipeline. Recorded in `IMPLEMENTATION_PLAN.md`.
