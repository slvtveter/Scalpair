# Scalpair — API (v1)

Base: `http://localhost:8080/api/v1` (nginx) or `:8000` (direct). OpenAPI: `/docs`.

## Public market data (no auth)
| Endpoint | Description |
|---|---|
| `GET /health` | feed mode, latency, ingest counters, ws clients, redis state |
| `GET /meta` | product info + safety statement |
| `GET /screeners/overview` | full scanner rows (price, 5m%, NATR, speed, surge, walls, level distance, score, tag, walls[]) |
| `GET /screeners/densities?limit=` | all walls sorted by notional desc |
| `GET /screeners/ai-picks?top=&min_score=` | top setups + thesis |
| `GET /alerts` (screeners feed) | recent auto score≥85 alerts |
| `GET /markets/symbols` | tracked universe |
| `GET /markets/{SYMBOL}/candles?limit=300` | 1m OHLCV + walls + pivot cascade levels |
| `WS /ws/live-feed` | snapshot stream ≤6 msg/s; first message `{"token": jwt}` → pro tier, else free (10 symbols, delayed) |

## Auth
| Endpoint | Description |
|---|---|
| `POST /auth/register` `{email, password}` | 201; 409 duplicate; rate-limited 10/min/IP |
| `POST /auth/login` | → `{access_token, subscription_tier}` (JWT HS256) |
| `GET /auth/me` | Bearer required |

## Alert rules (Bearer required)
| Endpoint | Description |
|---|---|
| `POST /alerts` | `{symbol, rule_type: price_cross_above\|price_cross_below\|pct_move\|volume_surge\|score_above, threshold, window_s?, cooldown_s?, recurring?}` |
| `GET /alerts` | user rules |
| `DELETE /alerts/{id}` | 404 if not owner |
| `GET /notifications` | in-app inbox: events + delivery statuses (`in_app:sent`, `telegram:sent\|failed\|unconfigured`) |
| `POST /telegram/link` | `{configured, link_url?}` — honestly `unconfigured` without `TELEGRAM_BOT_TOKEN` |
| `DELETE /telegram/link` | unlink |

## Semantics
- Crossing rules fire on the **edge** through the threshold (not while beyond it); `cooldown_s` re-arm; `recurring=false` disables after first fire; events idempotent by `event_id`.
- Alerts are evaluated **server-side every 2s** — they fire with the browser closed.
