# Деплой Scalpair на Render

## Вариант 1 — Blueprint (рекомендуется, ~5 минут)

1. Залейте репозиторий на GitHub (уже сделано: `slvtveter/Scalpair`).
2. https://dashboard.render.com → **New → Blueprint** → выберите репозиторий.
3. Render прочитает `render.yaml` и создаст:
   - `scalpair-backend` (Docker, health-check `/api/v1/health`, JWT_SECRET генерируется автоматически);
   - `scalpair-frontend` (static + rewrite `/api/*` → бэкенд, security headers).
4. Нажмите **Apply**. Через ~5–10 минут фронт будет на
   `https://scalpair-frontend.onrender.com` — WebSocket и API идут через тот же домен,
   так что ничего дополнительно настраивать не нужно.

## Вариант 2 — CLI

```bash
npm install -g @render-oss/cli   # или brew install render
render login                     # откроет браузер для авторизации
render blueprint launch          # применит render.yaml из репозитория
```

## Ограничения free-тира (важно для реального использования)

- Бесплатные web-сервисы **засыпают** после 15 минут без трафика — WS-фид
  перезапускается с cold start (~1–2 минуты: бэкфилл 300 свечей, прогрев скоринга).
  Для всегда-живого терминала нужен план **Starter** ($7/мес) на бэкенд.
- Free-tier нет persistent disk: пользователи и алерт-правила в SQLite сбрасываются
  при редеплое (рыночные данные в памяти — не страдают).
- Render static не проксирует WebSocket на free — если `wss://` не пройдёт через
  rewrite, используйте paid-план бэкенда и прямой WS-домен, либо деплойте фронт
  на Cloudflare Pages (бесплатный WS-проксинг).

## Переменные окружения

| Переменная | Значение |
|---|---|
| `DATA_FEED` | `auto` (binance → bybit → okx → mock) |
| `TOP_SYMBOLS` | 30 free / 50+ paid |
| `JWT_SECRET` | генерируется blueprint'ом |
| `TELEGRAM_BOT_TOKEN` | опционально; без него алерты остаются in-app |
