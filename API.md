# Scalpair — API (v1)

Base: `http://localhost:8080/api/v1` (nginx) or `:8000` (direct). OpenAPI: `/docs`.

## Public market data (no auth)
| Endpoint | Description |
|---|---|
| `GET /health` | feed mode, latency, ingest counters, ws clients, redis state |
| `GET /ready` | process readiness and market freshness state |
| `GET /metrics` | Prometheus-compatible ingest/feed counters |
| `GET /instruments?q=&exchange=&market=` | unified instrument catalog for search |
| `GET /meta` | product info + safety statement |
| `GET /screeners/overview` | full scanner rows (price, 5m%, NATR, speed, surge, market cap, walls, level distance, score, tag) |
| `GET /screeners/densities?limit=` | all walls sorted by notional desc, with BRP/category and explicit top-N depth coverage metadata |
| `GET /screeners/ai-picks?top=&min_score=` | top setups + thesis |
| `GET /screeners/score-alerts` | recent auto score≥85 alerts |
| `GET /markets/symbols` | tracked universe |
| `GET /markets/{SYMBOL}/candles?limit=300&instrument_id=` | OHLCV + walls + pivot cascade levels; an explicit BI-F/BY-F/OK-F `instrument_id` fetches that venue's history (source-specific overlays are omitted and marked by `overlaysAvailable=false`), mismatched identity returns 409 |
| `WS /ws/live-feed` | snapshot stream ≤6 msg/s; first message `{"token": jwt}` → pro tier, else free (10 symbols, delayed) |

## Auth
| Endpoint | Description |
|---|---|
| `POST /auth/register` `{email, password}` | 201; 409 duplicate; rate-limited 10/min/IP |
| `POST /auth/login` | → `{access_token, subscription_tier}` (JWT HS256) |
| `GET /auth/me` | Bearer required |
| `GET /preferences` | authenticated workspace settings and layout |
| `PUT /preferences` | authenticated workspace settings (groups, hidden, order, filters, layout, sound, `dndUntil` Unix seconds) |
| `GET /watchlists` | authenticated favorite symbols |
| `PUT /watchlists` | replace authenticated favorite symbols |

## Alert rules (Bearer required)
| Endpoint | Description |
|---|---|
| `POST /alerts` | `{symbol, rule_type: price_cross_above\|price_cross_below\|pct_move\|volume_surge\|score_above, threshold, window_s?, cooldown_s?, recurring?}` |
| `GET /alerts` | user rules (Bearer) |
| `PATCH /alerts/{id}` | update enabled, threshold, cooldown or recurring (owner only) |
| `DELETE /alerts/{id}` | 404 if not owner |
| `GET /notifications` | in-app inbox: events + delivery statuses (`in_app:sent`, `telegram:sent\|failed\|unconfigured`) |
| `POST /telegram/link` | `{configured, link_url?}` — honestly `unconfigured` without `TELEGRAM_BOT_TOKEN` |
| `DELETE /telegram/link` | unlink |

## Semantics

Scanner rows and instrument catalog entries expose `market_cap_usd`,
`market_cap_source` and `market_cap_updated_at` when the configured public
market-cap provider has a match. Missing values are `null`; they are never
derived from futures price or volume. The default provider is CoinGecko and
refreshes on a ten-minute interval; set `MARKET_CAP_ENABLED=false` to disable
the optional enrichment.

- Crossing rules fire on the **edge** through the threshold (not while beyond it); `cooldown_s` re-arm (min 30s enforced); `recurring=false` disables after first fire; each fire gets a unique `event_id` recorded in `alert_events` (restart-safe audit).
- When authenticated `dndUntil` is in the future, alert events are still recorded but delivery is stored as `in_app:muted`; Telegram and in-app sends are suppressed until that timestamp.
- Alerts are evaluated **server-side every 2s** — they fire with the browser closed.
