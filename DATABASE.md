# Scalpair — DATABASE

SQLite (WAL mode) через SQLAlchemy async; на PostgreSQL переносится заменой `DATABASE_URL`
(миграции Alembic — в дорожной карте, текущая схема создаётся `create_all`).

## Таблицы

| Таблица | Назначение |
|---|---|
| `users` | email, PBKDF2 hash (240k iter), `subscription_tier` (free/pro/whale) |
| `watchlist` | пользовательские списки (scaffolding) |
| `alert_log` | авто-события скоринга ≥85 (write-only журнал) |
| `alert_rules` | правила алертов: symbol, rule_type, threshold, window_s, cooldown_s, recurring, enabled, last_value (edge-якорь), last_triggered_at |
| `alert_events` | сработавшие события; `event_id` UNIQUE — идемпотентность |
| `notification_deliveries` | доставка по каналам: `in_app` (inbox), `telegram`; статус pending/sent/failed/unconfigured, attempts, last_error |
| `telegram_links` | user_id UNIQUE, chat_id, one-time link_token + срок |

## ER (упрощённо)

```
users 1─∞ alert_rules 1─∞ alert_events 1─∞ notification_deliveries
  │ 1─1 telegram_links
  └─∞ watchlist
```

Рыночные данные (стаканы, свечи, метрики) — **в памяти процесса** (`app/state.py`),
персистентность API-состояния — SQLite. Перенос OHLCV в Postgres/TimescaleDB — P4.
