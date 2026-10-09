# Scalpair — INDICATORS (точные формулы)

Все функции детерминированы и покрыты fixture-тестами (`tests/test_indicators.py`, `tests/test_levels.py`).

| Метрика | Формула | Реализация |
|---|---|---|
| True Range | `max(H−L, |H−Cprev|, |L−Cprev|)` | `indicators.true_range` |
| ATR (Wilder) | seed = SMA(TR, n); далее `ATR = (ATR×(n−1) + TR)/n`, n=14, ТФ 1m | `indicators.wilder_atr` |
| NATR % | `100 × ATR / Close` | `indicators.natr_pct` |
| Скорость цены, %/мин | `100 × (P_now/P_затем − 1) / Δt_мин`, окно 60 c, знак сохраняется | `indicators.price_speed_pct_per_min` (по 1с-истории цены) |
| Range % (5м) | `100 × (maxH − minL) / minL` за 5 последних свечей | `indicators.high_low_range_pct` |
| Всплеск объёма | объём **последней закрытой** 1m-свечи / среднее за предыдущие 20 закрытых | `indicators.volume_surge_closed` |
| Всплеск (внутриминутный) | V(1m) / mean(V за 30×1m) — для скоринга | `state.volume_surge_ratio` |
| Bid/Ask imbalance | `(ΣBids₁₀ − ΣAsks₁₀) / (ΣBids₁₀ + ΣAsks₁₀)` | `analytics.imbalance_ratio` |
| Стена (плотность) | уровень с notional ≥ `WALL_MULTIPLIER` × медиана notional уровней за ~5 мин | `analytics.detect_walls` |
| Время «съесть» стену | `notional / (V(5m)/300)` сек | `analytics.seconds_to_eat_wall` |
| NATR-дистанция до уровня | см. pivots/каскады ниже | `levels.nearest_cascade_distance_pct` |

## Пивоты и каскады (раздел 5 ТЗ)

- **Пивот** — confirmed swing high/low: строго больше/меньше `k=3` баров слева и справа.
  Подтверждение наступает только после закрытия k будущих баров (non-repainting).
- **Каскад (зона)** — кластер пивотов одного направления в допуске
  `epsilon = max(2×tickSize, price×0.0015, ATR×atr_multiplier)`, касания разделены ≥3 барами.
- Зона: low/high/mid, число касаний, тип support/resistance; сортировка по близости к цене.
- Уровни — эвристика, не гарантированная поддержка/сопротивление.

## AI Scalp Score (0–100)

12-факторный вектор (surge, imbalance, spread, depth, wall proximity/asymmetry,
BB-сжатие, скорость сделок, taker pressure, flow z-score, 5m momentum, funding bps) →
детерминированный квант-эвристик (работает сразу) + IsolationForest по накопленной
истории факторов (включается после ≥150 сэмплов). Финал: `0.65×heuristic + 0.35×anomaly`.
