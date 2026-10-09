# Scalpair — TEST PLAN

## Автоматические (pytest, 85 тестов — `cd backend && ./.venv/bin/python -m pytest`)

| Suite | Покрывает |
|---|---|
| `test_analytics.py` | imbalance (включая границы), wall detection (медиана×multiplier, сортировка, знак дистанции), seconds-to-eat, flow z-score, buckets/surge/taker/candle-dedup/BB |
| `test_indicators.py` | true range (3 случая), Wilder ATR (seed+recursive), NATR, signed speed, range, closed-candle surge, insufficient-data → None |
| `test_levels.py` | пивоты k=3: не подтверждается до k будущих баров; strict-двойная вершина; зона из 3 касаний; min_touches=1; distance |
| `test_ml_engine.py` | 12 факторов (форма/границы), heuristic 0–100, теги (Breakout Imminent и др.), IsolationForest (outlier > normal), ранжирование picks |
| `test_feeds.py` | парсеры Binance/Bybit (depth/aggTrade/kline/ticker, snapshot+delta, seq-инварианты), malformed frames never raise, stale-watchdog, mock-фид |
| `test_api.py` | health/meta, screeners (сортировки, формы), auth lifecycle + 409/401/422/429, security headers, candles + 404, WS (snapshot, free-tier trim, валидный токен, мусор, 1008 при невалидном токене) |
| `test_alerts.py` | edge-семантика: пересечение → ровно одно срабатывание; нахождение за порогом — без дублей; re-arm после cooldown; one-shot disable; идемпотентный event_id |

## Ручные проверки (факт)

- `docker compose up --build` на чистом демоне → 3 контейнера healthy.
- Живая проверка: гибрид `binance+bybit-trades`, 30 символов, ~23k msg/min, ingest latency 0 мс.
- Нагрузка: 20 RPS REST + 25 WS × 60 c → 0 ошибок, p50 6.9 мс / p95 16.1 мс / p99 26.9 мс.
- UI (браузер): RU-локализация, 15 колонок, доска 3×3 (9 тайлов, страницы), график (пан/зум/кроссхеир/ТФ 1m–1h/уровни каскадов/стены), фильтры, поиск, watchlist ★, звук, bell+login, создание алерта.
- Проверка поведения без браузера: правило `score_above` создано через API → событие появилось в `/notifications` при закрытом UI (engine тикает 2 с на сервере).

## Незакрытое (честно)

- Playwright smoke-набор (Screener → Board → Focus → Back) — в дорожной карте.
- 100-переходов Focus ↔ Screener leak-тест — не автоматизирован (код-ревью: listeners/queues ограничены).
- Нагрузка 200 символов / 16 графиков — после multi-venue.
