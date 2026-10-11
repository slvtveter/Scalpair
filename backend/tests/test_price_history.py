"""Time-based lookbacks must survive high trade throughput."""
import pytest

from app.indicators import price_speed_pct_per_min
from app.state import SymbolState, PRICE_HISTORY_KEEP_S


def test_busy_market_retains_minute_and_five_minute_lookbacks():
    state = SymbolState('BTCUSDT')
    start = 1_700_000_000_000
    for second in range(401):
        for tick in range(100):
            state.on_trade(start + second * 1000 + tick, 100 + second, 1, False)
    now = start + 400_000 + 99
    assert len(state.price_history) == 401
    assert state.price_change_5m(now) == pytest.approx(150)
    assert price_speed_pct_per_min(list(state.price_history), 60, now) == pytest.approx(13.6364)
    assert sum(bucket.count for bucket in state.buckets.values()) == 40_100


def test_history_is_bounded_and_ignores_out_of_order_samples():
    state = SymbolState('BTCUSDT')
    for second in range(PRICE_HISTORY_KEEP_S + 20):
        state.on_trade(second * 1000, 100 + second, 1, False)
    assert len(state.price_history) == PRICE_HISTORY_KEEP_S
    before = list(state.price_history)
    state.on_trade(1000, 999, 1, False)
    assert list(state.price_history) == before
    assert state.price_history[-1][1] == 100 + PRICE_HISTORY_KEEP_S + 19
