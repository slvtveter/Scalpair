# Scalpair — RUNBOOK

## Запуск / остановка
```bash
docker compose up --build      # backend :8000, frontend :8080, redis
docker compose logs -f backend # живые логи
docker compose down            # стоп (данные в volume backend-data)
```
Локальная разработка: `cd backend && ./.venv/bin/uvicorn app.main:app --reload` (UI тоже на :8000).
Тесты: `./.venv/bin/python -m pytest`. Нагрузка: `./.venv/bin/python scripts/loadtest.py --rps 20 --duration 60 --ws-clients 25`.

## Режимы фида
- `DATA_FEED=auto` (дефолт): проба Binance REST → Bybit REST → MockFeed.
- При fallback в mock (сеть ещё не поднялась) каждые 120 c идёт **recovery-проба** и горячий
  переход на live без рестарта (лог: `live feed recovered via …`).
- Гибрид: если основной фид отдаёт книги, но не отдаёт сделки (25 c, 0 trades) — поднимается
  supplementary Bybit-фид для сделок/свечей/фандинга (`binance+bybit-trades`).
- Явный `DATA_FEED=mock` — детерминированный оффлайн-демо режим, recovery отключён.

## Лимиты бирж
- Binance klines backfill: 30 запросов × 0.15 c пауза (weight ≈150 из 2400/мин).
- Bybit: подписки чанками по 10 аргументов; ping каждые 20 c.
- Все WS: exponential backoff (1.5^n + jitter, ≤60 c), staleness watchdog (15 c без сообщений → reconnect).

## Stataan resync (orderbook)
Bybit atomic `seq`: при пропуске (`seq != prev+1`) локальный стакан очищается и отправляется
повторный `subscribe orderbook.50.<SYM>` — биржа присылает свежий snapshot. До прихода
снимка UI помечает символ `stale` (книжная метка старше 15 c).

## Секреты
- `JWT_SECRET` — установить ≥32 случайных байт в проде (при дефолте — WARNING в логах).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_USERNAME` — только в env бэкенда. Без токена
  Telegram-доставки честно помечаются `unconfigured`, остальное работает.
- Никаких биржевых ключей: система read-only, приватные данные не запрашиваются никогда.

## База
SQLite WAL в volume `backend-data`; entrypoint чинит владельца файла и роняет права на
non-root `app`. Перенос: заменить `DATABASE_URL` на PostgreSQL URL (схема совместима).
