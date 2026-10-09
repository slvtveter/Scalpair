# Scalpair — IMPLEMENTATION PLAN

Status: **P1 vertical slice delivered and verified** against `Scalpair_Spec_EN.md` / `Scalpair_TZ_RU.md`.
This file records what is genuinely implemented versus what remains, per the spec's honesty rules.

## Implemented (verified by automated tests + live smoke tests)

| Spec area | Status | Evidence |
|---|---|---|
| Live USDT-perp screener, top-30 by volume, 60s universe refresh | done | `/api/v1/screeners/overview`, hybrid Binance books + Bybit trades |
| L2 order books (Binance partial depth snapshots; Bybit snapshot+delta maintenance) | done | feeds + wall detection (`3×median`), distance %, seconds-to-eat |
| Trades/aggTrade ingestion, per-minute aggregation, taker pressure, flow z-score | done | unit tests (analytics) |
| 12-factor ML pipeline → Scalp Score 0–100 + tags + thesis | done | unit tests (heuristic bounds, tag rules, IsolationForest blend) |
| REST API: health / densities / ai-picks / overview / alerts / symbols / auth / candles | done | 64 pytest, load test |
| WebSocket fanout `/api/v1/ws/live-feed`, throttled, tiered (free/pro) | done | WS QA agent report, load test 25 clients |
| JWT auth scaffolding, subscription tiers, rate limiting, security headers | done | pytest auth + 429 + headers |
| Interactive chart: 1m candles (REST backfill 300 bars), pan/zoom/crosshair/TF picker, wall levels, volume, live refresh | done | browser-verified (pan/zoom pixel-hash) |
| Terminal UI: scanner table + trend sparklines + hotlist + density radar + alerts, minimal dark theme | done | browser-verified, WCAG AA contrast checks |
| Resilience: reconnect/backoff/staleness watchdog, feed degradation → hybrid Bybit supplement, offline MockFeed | done | live hybrid activation in Docker logs |
| Load: 20 RPS REST + 25 WS clients → 0 errors, p95 16 ms | done | `scripts/loadtest.py` |

## Remaining (recorded, not claimed)

- **P1 gaps**: 3×3 chart board grid (currently single-focus modal chart); multi-venue tiles (BI-F/BI-S/BY-F/BY-S identity model); NATR / speed / market-cap columns; search beyond tracked top-N.
- **P2**: watchlists/groups/hidden, user preferences persistence, drawings, sortable presets.
- **P3**: server-side alert rules engine (browser-closed triggers), Telegram linking, pivot/cascade levels, liquidity map UI, L2 for arbitrary visible instruments.
- **P4**: OKX adapter, spot markets, BTC correlation, full-depth coverage study.
- **Stack migration** (spec asks React/TS/Vite/lightweight-charts/Postgres/Alembic): current MVP is a deliberate FastAPI + vanilla-JS vertical slice; migration is P2+ work, recorded as architectural debt, not hidden.

## Next milestones (recommended order)

1. Pivot/cascade engine (deterministic, tested) + level overlays on chart + proximity sort.
2. Server-side alert rules (price cross, surge, density appear) with durable queue + in-app inbox.
3. Chart board grid (1×1/2×2/3×3) reusing the existing candle renderer.
4. React/TS + lightweight-charts migration behind the existing API contracts.
5. Postgres + Alembic persistence for users/preferences/drawings; Redis Streams delivery queue.
